"""Response parsing — JSON-mode, fenced-block streaming, and suggestions.

The architect endpoint accepts two response shapes:
1. JSON-mode (non-streaming): the whole reply is one JSON object.
2. Streaming: conversational text followed by ``` ``` fenced blocks
   (``automation``, ``scene``, ``command``, ``delayed_command``, ``cancel``,
   ``quick_actions``) that the renderer parses incrementally.

This module owns parsing for both shapes plus the suggestions array, and
applies scene/automation validation. Command-policy validation is layered
on top by the caller (see ``command_policy.apply_command_policy``).
"""

from __future__ import annotations

import copy
import json
import logging
import re
from typing import TYPE_CHECKING, Any

import yaml

from ..automation_utils import (
    _collect_referenced_entity_ids,
    _find_unknown_entity_ids,
    assess_automation_risk,
    validate_automation_payload,
)
from ..lexical import (
    KW_FUZZY_FLOOR,
    KW_HELPER_PENALTY,
    KW_MIN_MARGIN,
    KW_W_FUZZY,
    KW_W_HINT,
    KW_W_OVERLAP,
    fuzzy_ratio,
    normalize,
)
from ..telemetry import record_repair
from ..types import ArchitectResponse, EntitySnapshot
from .command_policy import (
    _call_signature,
    _executed_call_signatures,
    _executed_service_calls_from_log,
    _prose_describes_attempted_call,
    _prose_is_trusted_after_tool,
    _response_names_unbacked_entity,
    apply_command_policy,
    synthesize_approval_from_tool_log,
)
from .intent import _is_definite_automation

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

# Inline entity tile markers (`[[entity:…]]` / `[[entities:…]]`) belong
# in answer-style replies where the user wants to see live device
# state. Automation and scene proposals describe a *rule*, not the
# current state of a device — embedding a live tile in those bubbles
# (as some models, notably deepseek, like to do regardless of the
# prompt) shoves an irrelevant brightness slider into the proposal
# card. Strip the markers from the prose before we hand it off.
_ENTITY_MARKER_RE = re.compile(r"\s*\[\[(?:entity|entities|areas):[^\]]+\]\]")


def _find_bare_block(text: str, type_word: str) -> tuple[str, int, int] | None:
    """Locate a fence-less ``<type_word>\\n{...}`` block and decode the
    first balanced JSON object starting at the ``{``.

    Returns ``(json_text, start, end)`` where ``start``/``end`` bound the
    whole block (the type-word line through the JSON object, plus an
    optional trailing ```` ``` ```` fence) so the caller can splice the
    surrounding prose; ``None`` when no bare block is present or the JSON
    is malformed.

    A balanced ``raw_decode`` — not a greedy ``\\{[\\s\\S]*\\}`` regex —
    so trailing prose containing a ``}`` (a follow-up sentence with
    ``{}``, a Jinja ``{{ … }}`` snippet, …) cannot extend the capture
    past the automation/scene object and break ``json.loads``.

    Matches enclosed by an existing ```` ``` ```` fence are skipped: a
    generic fenced example (```` ```\nautomation\n{...}\n``` ````, shown
    when the user asks to see the JSON shape) is illustrative code, not a
    proposal — salvaging it would strip the example from the answer and
    surface a spurious Accept card. An odd ```` ``` ```` count before the
    candidate means it sits inside a fence.
    """
    for m in re.finditer(
        rf"(?:^|\n)[ \t]*{type_word}[ \t]*\n[ \t]*(?=\{{)",
        text,
    ):
        if text.count("```", 0, m.start()) % 2 == 1:
            continue  # inside a fenced code example — not a real block
        brace = m.end()
        try:
            _obj, end = json.JSONDecoder().raw_decode(text, brace)
        except ValueError:
            continue
        json_text = text[brace:end]
        # Absorb an optional closing fence sitting right after the object
        # so it doesn't survive as a stray ``` in the spliced prose.
        fence = re.match(r"[ \t]*\n?[ \t]*```[ \t]*", text[end:])
        block_end = end + (fence.end() if fence else 0)
        return json_text, m.start(), block_end
    return None


def _strip_entity_markers(text: str) -> str:
    """Remove `[[entity:…]]` / `[[entities:…]]` markers from response prose."""
    if not isinstance(text, str) or not text:
        return text
    cleaned = _ENTITY_MARKER_RE.sub("", text)
    if cleaned != text:
        record_repair("friendly_name_strip")
    # Collapse runs of trailing blank lines the marker removal left
    # behind so the response doesn't end with a stretch of empty
    # whitespace on the rendered card.
    return re.sub(r"\n{3,}", "\n\n", cleaned).rstrip()


def _strip_markers_for_proposal_intents(data: dict[str, Any]) -> None:
    """Strip entity tile markers from automation/scene proposal prose.

    Mutates ``data["response"]`` in place. Called for any reply whose
    *original* intent indicated a creation flow — i.e. the model said
    intent: automation / scene, OR a payload (`automation`/`scene`)
    was present even if the JSON didn't carry the intent explicitly.
    The post-validation downgrade to ``answer`` (when the payload is
    invalid) doesn't matter — by that point we've already overwritten
    the response with a validation-error message that has no markers.
    """
    if "response" not in data:
        return
    intent = data.get("intent")
    if intent in ("automation", "scene") or data.get("automation") or data.get("scene"):
        data["response"] = _strip_entity_markers(data["response"])
        data["response"] = _strip_trigger_action_recap(data["response"])


# Lines like "Trigger: …", "Condition: …", "Action: …" (and their
# plural variants) restate the YAML in prose — pure duplication of
# the proposal card rendered just below the bubble. The streaming
# prompt explicitly forbids these but models occasionally slip them
# in anyway, especially deepseek-style models that like to summarise
# their own JSON output back as text. Strip the lines server-side so
# the bubble stays focused on the human framing of what the rule
# does.
_RECAP_LINE_RE = re.compile(
    r"^\s*(?:[-*•]\s*|\d+[.)]\s*)?\**\s*"
    r"(?:Triggers?|Conditions?|Actions?|Trigger\s+condition|Trigger\s+state)"
    r"\s*\**\s*:\s.*$",
    re.IGNORECASE | re.MULTILINE,
)


def _strip_trigger_action_recap(text: str) -> str:
    """Remove ``Trigger:`` / ``Condition:`` / ``Action:`` recap lines."""
    if not isinstance(text, str) or not text:
        return text
    cleaned = _RECAP_LINE_RE.sub("", text)
    if cleaned != text:
        record_repair("state_info_strip")
    # Collapse the blank stretch the line removal leaves behind.
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.rstrip()


# Map each service action to a semantic verb so an entity swap across
# domains (lock.lock → cover.close_cover) preserves user intent.
_SERVICE_ACTION_VERB: dict[str, str] = {
    "turn_on": "on",
    "open_cover": "on",
    "open": "on",
    "unlock": "on",
    "start": "on",
    "activate": "on",
    "enable": "on",
    "turn_off": "off",
    "close_cover": "off",
    "close": "off",
    "lock": "off",
    "stop": "off",
    "deactivate": "off",
    "disable": "off",
    "pause": "off",
}

_DOMAIN_VERB_TO_ACTION: dict[str, dict[str, str]] = {
    "light": {"on": "turn_on", "off": "turn_off"},
    "switch": {"on": "turn_on", "off": "turn_off"},
    "fan": {"on": "turn_on", "off": "turn_off"},
    "climate": {"on": "turn_on", "off": "turn_off"},
    "media_player": {"on": "turn_on", "off": "turn_off"},
    "vacuum": {"on": "start", "off": "stop"},
    "scene": {"on": "turn_on", "off": "turn_on"},
    "input_boolean": {"on": "turn_on", "off": "turn_off"},
    "cover": {"on": "open_cover", "off": "close_cover"},
    "lock": {"on": "unlock", "off": "lock"},
}


def _service_verb_for_domain(verb: str, domain: str) -> str:
    """Pick the right service action for ``domain`` given an "on"/"off" verb."""
    table = _DOMAIN_VERB_TO_ACTION.get(domain)
    if table is None:
        return f"turn_{verb}"
    return table.get(verb, f"turn_{verb}")


# Min friendly_name overlap with prompt — short names ("Sun") match too liberally.
_PROMPT_FNAME_MIN_LEN = 4

# Controllable-device domains. Auto-correct only rewrites a bad
# entity_id when both the bad and the candidate sit in this set, so
# helper-class entities (input_boolean, sensor mirrors, etc.) never
# get silently substituted for a real device — or vice versa. The
# presence + duration aggressive-substitution path also uses this set
# to prefer real-device candidates over helpers when prompt-keyword
# overlap is the only available signal.
_REAL_DEVICE_DOMAINS: frozenset[str] = frozenset(
    {
        "light",
        "switch",
        "cover",
        "lock",
        "climate",
        "fan",
        "media_player",
        "vacuum",
        "scene",
        "alarm_control_panel",
        "water_heater",
    }
)


# Keys whose subtrees are integration-defined payload, NOT HA entity
# references. An ``entity_id`` inside ``event_data`` (event trigger
# filter), ``event_data_template``, ``payload`` (MQTT trigger), or
# ``payload_template`` is application data the user defines for matching
# events — it must not be substituted or collected as an HA entity ref.
# The matching authoritative collector in automation_utils ignores them.
_NON_ENTITY_PAYLOAD_KEYS: frozenset[str] = frozenset(
    {
        "event_data",
        "event_data_template",
        "payload",
        "payload_template",
        "variables",
        "response_variable",
        "metadata",
    }
)


def _entities_named_in_prompt(
    user_message: str,
    entities: list[EntitySnapshot] | None,
) -> list[str]:
    """Return entity_ids whose friendly_name OR slug_words are all named
    in ``user_message``, longest-first.

    A candidate matches when every content token of its friendly_name (or
    of its slug) is present in the prompt token set. This is order- and
    particle-independent, so it survives function words inserted between
    name words — "allume la lumière du salon" matches the entity "Lumière
    Salon" even though "lumière salon" is not a contiguous substring. A
    plain substring check (the previous approach) broke on exactly that:
    it works for English ("the living room light") only because English
    rarely interleaves particles, but fails for fr/de/es/it ("la lumière
    DU salon", "das Licht IM Wohnzimmer", "la luz DEL salón").

    Requiring the FULL token set keeps precision: "Living Room Fan" does
    not match "turn off the living room light" (no "fan" in the prompt),
    so the single-hit caller never silently retargets the wrong device.
    """
    if not entities or not user_message:
        return []
    prompt_tokens = set(normalize(user_message).split())
    hits: list[tuple[int, str]] = []
    for e in entities:
        eid = e.get("entity_id", "")
        if "." not in eid:
            continue
        fname = normalize(str((e.get("attributes") or {}).get("friendly_name") or ""))
        # normalize() casefolds and turns slug underscores into spaces, so
        # accented friendly names match across the fr/de/es/it locales.
        slug_words = normalize(eid.split(".", 1)[1])
        # Prefer the longest matching name — a fname match is usually more
        # specific than slug_words; the slug fallback catches the
        # "Living Room RGBWW Lights" vs entity-fname "Living Room Light"
        # mismatch where the room-stem tokens still appear in the prompt.
        matched_len = 0
        for name in (fname, slug_words):
            name_tokens = name.split()
            if not name_tokens or set(name_tokens) - prompt_tokens:
                continue
            joined = " ".join(name_tokens)
            if len(joined) >= _PROMPT_FNAME_MIN_LEN:
                matched_len = max(matched_len, len(joined))
        if matched_len == 0:
            continue
        hits.append((matched_len, eid))
    hits.sort(key=lambda x: -x[0])
    seen: set[str] = set()
    out: list[str] = []
    for _, eid in hits:
        if eid in seen:
            continue
        seen.add(eid)
        out.append(eid)
    return out


# Content tokens we strip when scoring entity ↔ prompt keyword overlap.
# Same shape as the low-context stopword filter in intent.py but kept
# local so parsers.py stays import-free of that module.
_PROMPT_KEYWORD_STOPWORDS = frozenset(
    {
        "the",
        "a",
        "an",
        "is",
        "are",
        "was",
        "were",
        "be",
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
        "we",
        "us",
        "you",
        "i",
        "me",
        "it",
        "if",
        "when",
        "whenever",
        "while",
        "then",
        "now",
        "just",
        "as",
        "do",
        "does",
        "did",
        "can",
        "could",
        "would",
        "should",
        "will",
        "shall",
        "have",
        "has",
        "had",
        "please",
        "thanks",
        "no",
        "one",
        "noone",
        "nobody",
        "someone",
        "anyone",
        "anybody",
        "everyone",
        "minute",
        "minutes",
        "second",
        "seconds",
        "hour",
        "hours",
        "turn",
        "off",
        "set",
        "make",
        "switch",
        "open",
        "close",
        "lock",
        "unlock",
        "start",
        "stop",
        "run",
        "all",
        "every",
        "each",
    }
)

# Multilingual function words (3+ chars; shorter articles like "le"/"el"/
# "il" are already dropped by the len>=3 filter). Conversational queries
# follow hass.config.language, so French/German/Spanish/Italian fillers
# must not create spurious entity-name token overlap in the keyword
# picker the way English fillers are stripped above.
_PROMPT_KEYWORD_STOPWORDS = _PROMPT_KEYWORD_STOPWORDS | frozenset(
    {
        # French
        "dans",
        "avec",
        "pour",
        "cette",
        "ces",
        "leur",
        "leurs",
        "notre",
        "votre",
        "mais",
        "donc",
        "sans",
        "chez",
        "sur",
        "sous",
        "entre",
        "tous",
        "tout",
        "toute",
        "toutes",
        "quel",
        "quelle",
        "quels",
        "quelles",
        "est",
        "sont",
        "très",
        "plus",
        "aussi",
        "allumé",
        "allumée",
        "allumés",
        "allumées",
        "éteint",
        "éteinte",
        "éteints",
        "éteintes",
        "allume",
        "éteins",
        "ferme",
        "ouvre",
        # German
        "der",
        "die",
        "das",
        "den",
        "dem",
        "ein",
        "eine",
        "einen",
        "und",
        "oder",
        "aber",
        "mit",
        "von",
        "für",
        "auf",
        "aus",
        "bei",
        "nach",
        "über",
        "unter",
        "ist",
        "sind",
        "welche",
        "welcher",
        "welches",
        "alle",
        "jede",
        "jeden",
        "bitte",
        # Spanish
        "las",
        "los",
        "una",
        "unas",
        "unos",
        "con",
        "por",
        "para",
        "pero",
        "sin",
        "sobre",
        "bajo",
        "todos",
        "todas",
        "cual",
        "cuales",
        "que",
        "qué",
        "está",
        "están",
        "este",
        "esta",
        "estos",
        "estas",
        "más",
        "encendido",
        "encendida",
        "encendidos",
        "encendidas",
        "apagado",
        "apagada",
        # Italian
        "della",
        "dello",
        "delle",
        "degli",
        "per",
        "uno",
        "gli",
        "sopra",
        "sotto",
        "tra",
        "fra",
        "tutti",
        "tutte",
        "quale",
        "quali",
        "che",
        "sono",
        "questa",
        "questo",
        "questi",
        "queste",
        "molto",
        "acceso",
        "accesa",
        "accese",
        "accesi",
        "spento",
        "spenta",
    }
)


def _prompt_keyword_best_entity(
    user_message: str,
    entities: list[EntitySnapshot] | None,
    *,
    domain_hint: str | None = None,
) -> str | None:
    """Pick the controllable entity whose slug/fname tokens best overlap
    the prompt. Used as the LAST-DITCH substitution when the LoRA
    hallucinated an entity (typically ``lock.front_door``) the existing
    by-slug / by-friendly_name / one-shot prompt-name lookups all miss.

    Scoring is a weighted ensemble: shared content-token overlap plus an
    order-insensitive fuzzy ratio (so "lite"→"light" typos and reordered
    words still land), with a ``domain_hint`` bonus and a helper-domain
    penalty so a verb-and-category prompt ("lock the front door for 3
    minutes") prefers a same-domain real device.

    Returns ``None`` when no candidate clears the overlap/fuzzy floor, OR
    when the top candidate does not beat the next distinct candidate by at
    least :data:`KW_MIN_MARGIN` — an ambiguous best match ("Front Porch
    Light" vs "Back Porch Light" for "the porch light") must fall through
    to clarification rather than pick whichever entity_id sorts first.
    """
    if not user_message or not entities:
        return None
    prompt_norm = normalize(user_message)
    raw_tokens = {t for t in prompt_norm.split() if t}
    prompt_tokens = {t for t in raw_tokens if len(t) >= 3 and t not in _PROMPT_KEYWORD_STOPWORDS}
    if not prompt_tokens:
        return None
    ranked: list[tuple[float, str]] = []
    for e in entities:
        eid = e.get("entity_id", "")
        if "." not in eid:
            continue
        domain, slug = eid.split(".", 1)
        slug_norm = normalize(slug)
        fname_norm = normalize(str((e.get("attributes") or {}).get("friendly_name") or ""))
        cand_tokens = set(slug_norm.split()) | set(fname_norm.split())
        overlap = cand_tokens & prompt_tokens
        fuzzy = max(
            fuzzy_ratio(prompt_norm, fname_norm),
            fuzzy_ratio(prompt_norm, slug_norm),
        )
        if not overlap and fuzzy < KW_FUZZY_FLOOR:
            continue
        score = KW_W_OVERLAP * (len(overlap) / len(prompt_tokens)) + KW_W_FUZZY * fuzzy
        if domain_hint and domain == domain_hint:
            score += KW_W_HINT
        if domain not in _REAL_DEVICE_DOMAINS:
            score -= KW_HELPER_PENALTY
        ranked.append((score, eid))
    if not ranked:
        return None
    # eid intentionally NOT a sort tie-breaker so a near-tie is detectable
    # via the margin gate and refused rather than picked by id order.
    ranked.sort(key=lambda r: -r[0])
    top_score, top_eid = ranked[0]
    if len(ranked) > 1 and (top_score - ranked[1][0]) < KW_MIN_MARGIN:
        # Ambiguous — top does not clearly beat the runner-up.
        return None
    return top_eid


def _resolve_unknown_entity_ids(
    reason: str,  # noqa: ARG001 -- callers gate on this; signature is the contract
    automation: dict[str, Any],
    entities: list[EntitySnapshot] | None,
    hass: HomeAssistant | None = None,
    user_message: str | None = None,
    *,
    aggressive: bool = False,
) -> tuple[dict[str, Any] | None, dict[str, str]]:
    """Auto-correct an "unknown entity_id" rejection by looking up each
    bad entity_id by slug + friendly_name across the snapshot. Only
    rewrites controllable-device domains and only when slug/fname maps
    to exactly one real device — partial matches fall through (silent
    half-fixes are worse than a clear ask).

    When ``user_message`` is provided AND ``aggressive`` is set (the
    presence + duration recovery path), the resolver additionally falls
    back to prompt-name / prompt-keyword matching for bad ids whose
    slug/fname yields no real-device candidate. The LoRA reliably
    hallucinates ``lock.front_door`` for those prompts even when the
    prompt's actual subject is "the heater" or "the porch light";
    falling back to any controllable entity that shares a token with
    the prompt is strictly better than dead-ending on a "couldn't build
    that" clarification when the user clearly asked for an automation.

    Returns ``(patched_or_None, substitutions)``."""
    if not entities:
        return None, {}

    by_slug: dict[str, list[str]] = {}
    by_fname: dict[str, list[str]] = {}
    real_ids: set[str] = set()
    for e in entities:
        eid = e.get("entity_id", "")
        if "." not in eid:
            continue
        real_ids.add(eid)
        slug = eid.split(".", 1)[1]
        by_slug.setdefault(slug, []).append(eid)
        fname = ((e.get("attributes") or {}).get("friendly_name") or "").lower().strip()
        if fname:
            by_fname.setdefault(fname, []).append(eid)

    # Walk the automation for the FULL set of referenced entity_ids
    # rather than parsing the validator reason — the validator truncates
    # its preview to the first three (with a ``(+N more)`` suffix), so a
    # regex over the reason string would miss the rest and a single
    # retry would fail on them.
    # Use the validator's own context-aware walker (state-machine
    # contexts only: trigger / condition / action target / data /
    # entity_id). Integration payloads like ``event_data: {entity_id:
    # ...}`` are skipped — those are user-defined match payloads, not
    # HA entity references.
    referenced = _collect_referenced_entity_ids(automation)
    # Ground truth for "unknown": delegate to the same lookup the
    # validator uses (``_find_unknown_entity_ids``) — state machine
    # AND entity registry. The snapshot filters out unavailable /
    # ignored / non-actionable entities; the registry covers
    # disabled-but-real entities that have no state but exist. Using
    # only ``hass.states`` would treat registered-but-stateless entities
    # as unknown and may silently retarget them. Fall back to
    # snapshot-only comparison when no ``hass`` is passed (unit tests).
    if hass is not None:
        bad_ids = _find_unknown_entity_ids(hass, set(referenced))
    else:
        bad_ids = sorted(referenced - real_ids)
    if not bad_ids:
        return None, {}

    # Fallback target derived from the prompt — used when slug/fname
    # lookup yields zero candidates but the prompt names exactly one
    # entity. Cheap to compute even in non-aggressive mode.
    prompt_fallback: str | None = None
    if user_message:
        prompt_hits = _entities_named_in_prompt(user_message, entities)
        if len(prompt_hits) == 1:
            prompt_fallback = prompt_hits[0]

    # Entity_ids referenced inside TRIGGER blocks. The prompt-name
    # fallback names the ACTION-target device ("the kitchen light"), so
    # substituting it into a trigger would silently retarget the trigger
    # — e.g. a hallucinated ``light.front_door`` trigger for "turn on
    # the kitchen light when the front door opens" must NOT become a
    # ``light.kitchen`` trigger. Track trigger eids so the fallback is
    # skipped for them.
    trigger_entity_ids: set[str] = set()

    def _walk_eids(node: Any) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                if k == "entity_id":
                    if isinstance(v, str):
                        trigger_entity_ids.add(v)
                    elif isinstance(v, list):
                        trigger_entity_ids.update(x for x in v if isinstance(x, str))
                else:
                    _walk_eids(v)
        elif isinstance(node, list):
            for item in node:
                _walk_eids(item)

    for tr in _read_triggers(automation):
        _walk_eids(tr)

    substitutions: dict[str, str] = {}
    for bad in bad_ids:
        if bad in real_ids:
            continue  # validator complained about something else, not domain
        bad_domain, bad_slug = bad.split(".", 1)
        # Compatibility gate: only auto-correct when the bad entity_id
        # itself names a controllable-device domain. A
        # ``person.garage_door`` trigger that shares its slug with
        # ``cover.garage_door`` must NOT be silently rewritten — the
        # automation would then fire on the cover's state changes
        # instead of the person arriving, which is a semantic change,
        # not a typo fix. Same for zone/device_tracker/sensor triggers.
        # The aggressive path (presence + duration recovery) relaxes
        # this gate because the prompt's intent is already pinned down
        # by the surrounding regex — the LoRA echoing ``lock.front_door``
        # in that context is a hallucination, not a real cross-domain
        # trigger.
        if not aggressive and bad_domain not in _REAL_DEVICE_DOMAINS:
            return None, {}
        candidates: set[str] = set(by_slug.get(bad_slug, []))
        candidates |= set(by_fname.get(bad_slug.replace("_", " ").lower(), []))
        # Slug-stem broadening (aggressive only) — the v0.4.7 LoRA
        # reliably appends the device category to a room slug when
        # emitting an action target ("light.living_room_light" instead
        # of "light.living_room"), and the user's fixture often has
        # BOTH a real-device entity at the shorter slug AND a state-
        # mirror helper at the full slug (input_boolean.living_room_light,
        # fname "Living room light relay"). Without this, the exact-slug
        # match grabs the input_boolean helper alone, len(candidates)==1
        # short-circuits the "prefer real domain" pass, and the helper
        # survives — its fname/slug doesn't appear in the prompt and the
        # bench's ``target_friendly_name_in_prompt`` check rejects it.
        if (
            aggressive
            and "_" in bad_slug
            and bad_domain in _REAL_DEVICE_DOMAINS
            and candidates
            and not any(c.split(".", 1)[0] in _REAL_DEVICE_DOMAINS for c in candidates)
        ):
            stem = bad_slug.rsplit("_", 1)[0]
            stem_hits = by_slug.get(stem, [])
            stem_fname_hits = by_fname.get(stem.replace("_", " ").lower(), [])
            for cand in (*stem_hits, *stem_fname_hits):
                if cand.split(".", 1)[0] in _REAL_DEVICE_DOMAINS:
                    candidates.add(cand)
        # Candidate side of the gate: substitution target must also be a
        # controllable-device domain, so we never retarget e.g.
        # ``light.x`` onto an ``input_boolean.x`` helper that only
        # mirrors state. In aggressive mode we still apply the same
        # constraint — the prompt-keyword fallback below picks up the
        # cases where this filter empties the candidate set.
        real_candidates = {c for c in candidates if c.split(".", 1)[0] in _REAL_DEVICE_DOMAINS}
        if len(real_candidates) == 1:
            substitutions[bad] = next(iter(real_candidates))
            continue
        if len(real_candidates) > 1 and aggressive:
            # Multiple real-device candidates — disambiguate using the
            # prompt-keyword winner (gives same-domain entities a fixed
            # bonus). Falls through to refuse if the winner isn't in
            # the candidate set.
            keyword = _prompt_keyword_best_entity(
                user_message or "", entities, domain_hint=bad_domain
            )
            if keyword is not None and keyword in real_candidates:
                substitutions[bad] = keyword
                continue
        # Zero candidates from slug/fname — try the prompt-name fallback
        # when the prompt names exactly one entity. Two guards:
        #   * Skip when the unknown id sits in a trigger — the
        #     prompt-named device is the action target, and retargeting a
        #     trigger to it changes WHEN the automation fires.
        #   * Skip when the prompt-named device is ITSELF the trigger
        #     entity — "When the Front Door opens, turn on a light" names
        #     only ``cover.front_door`` (the trigger); using it to fill an
        #     unknown action target would make the automation act on the
        #     trigger device. Refuse → clarification instead.
        if (
            not real_candidates
            and prompt_fallback is not None
            and bad not in trigger_entity_ids
            and prompt_fallback not in trigger_entity_ids
        ):
            substitutions[bad] = prompt_fallback
            continue
        # Last-ditch token-overlap (aggressive only). Constrained to the
        # SAME domain as the unknown entity to prevent silently
        # retargeting an action onto a different device class: a missing
        # ``light.porch`` must not be rewritten to ``fan.porch`` or
        # ``switch.porch_relay`` just because they share a prompt token.
        # Same-domain replacements ("light.porch" → "light.porch_main")
        # are the only token-overlap swap that preserves the user's
        # device-class intent without an explicit confirmation step.
        if not real_candidates and aggressive and bad_domain and bad not in trigger_entity_ids:
            fallback = _prompt_keyword_best_entity(
                user_message or "", entities, domain_hint=bad_domain
            )
            if (
                fallback is not None
                and fallback.split(".", 1)[0] == bad_domain
                and fallback not in trigger_entity_ids
            ):
                substitutions[bad] = fallback
                continue
        # Zero candidates, or more than one — refuse rather than guess.
        # Retargeting to "the one device the prompt names elsewhere"
        # would silently rewrite an unrelated trigger onto an unrelated
        # device and is intentionally not attempted in non-aggressive
        # mode.
        return None, {}

    if not substitutions:
        return None, {}

    # Intent-inversion and incompatible-service-data gates ALWAYS run,
    # even in aggressive mode. A swap that silently performs the OPPOSITE
    # action (``light.turn_off`` on ``light.movie`` → ``scene.turn_on``
    # because scenes have no off verb) or that produces a runtime failure
    # (brightness data on a switch) is never the right recovery — the
    # prompt's "turn off" intent is unambiguous and a valid-but-inverted
    # automation is worse than dead-ending to a clarification.
    if _substitution_inverts_intent(automation, substitutions):
        return None, {}
    if _substitution_drops_required_service_data(automation, substitutions):
        return None, {}

    # Pinned-state and mixed-domain gates are relaxed in aggressive mode:
    # presence + duration recovery often makes a deliberate cross-domain
    # swap (``light.porch`` → ``input_boolean.porch_light`` for "turn
    # off the porch light") that these would otherwise block, and the
    # surrounding prompt context already pins the intent.
    if not aggressive:
        # Refuse cross-domain substitutions that would leave a state
        # trigger or condition pinned to a value the new domain never
        # reports. A ``lock.front_door`` trigger with ``to: locked``
        # resolves to a ``cover.front_door`` — covers never emit
        # ``locked``, so the automation would silently never fire.
        cross_domain_subs = {
            bad: new
            for bad, new in substitutions.items()
            if bad.split(".", 1)[0] != new.split(".", 1)[0]
        }
        if cross_domain_subs and _bad_has_pinned_state_semantics(
            automation, set(cross_domain_subs)
        ):
            return None, {}

        # Refuse a substitution that would leave an action's ``target``
        # entity_id list straddling multiple domains. Such a list survives
        # validation but the action's service-domain can only address one
        # domain at runtime — the off-domain entries silently no-op or
        # fail.
        if _substitution_yields_mixed_domain_target(automation, substitutions):
            return None, {}

    patched: dict[str, Any] = copy.deepcopy(automation)
    _apply_entity_substitutions(patched, substitutions)
    return patched, substitutions


# Service-call data fields that only make sense for entities in a
# specific domain. When auto-correct moves an entity across domains,
# any of these keys present in the action's data refuse the swap — the
# new domain's service won't accept them, so the automation would pass
# validation but fail at runtime.
_DOMAIN_SPECIFIC_SERVICE_DATA: dict[str, frozenset[str]] = {
    "light": frozenset(
        {
            "brightness",
            "brightness_pct",
            "brightness_step",
            "brightness_step_pct",
            "color_name",
            "color_temp",
            "color_temp_kelvin",
            "effect",
            "flash",
            "hs_color",
            "kelvin",
            "profile",
            "rgb_color",
            "rgbw_color",
            "rgbww_color",
            "transition",
            "white",
            "xy_color",
        }
    ),
    "cover": frozenset({"position", "tilt_position"}),
    "climate": frozenset(
        {
            "fan_mode",
            "humidity",
            "hvac_mode",
            "preset_mode",
            "swing_mode",
            "target_temp_high",
            "target_temp_low",
            "temperature",
        }
    ),
    "fan": frozenset({"direction", "oscillating", "percentage", "preset_mode"}),
    "media_player": frozenset(
        {
            "media_content_id",
            "media_content_type",
            "shuffle",
            "sound_mode",
            "source",
            "volume_level",
        }
    ),
    "lock": frozenset({"code"}),
    "vacuum": frozenset({"params"}),
}


def _substitution_drops_required_service_data(node: Any, substitutions: dict[str, str]) -> bool:
    """Return True if applying ``substitutions`` would leave an action
    carrying service-data keys that only make sense for its previous
    (source) domain. Validator accepts unknown extra keys; runtime
    rejects them. Refusing the substitution surfaces the issue back to
    the user instead of producing a silently broken automation."""
    if isinstance(node, dict):
        eids: list[str] = []
        eids.extend(_ids_from_entity_field(node.get("entity_id")))
        tgt = node.get("target")
        if isinstance(tgt, dict):
            eids.extend(_ids_from_entity_field(tgt.get("entity_id")))
        data = node.get("data")
        if isinstance(data, dict):
            eids.extend(_ids_from_entity_field(data.get("entity_id")))
        for e in eids:
            new = substitutions.get(e)
            if new is None:
                continue
            src_domain = e.split(".", 1)[0]
            new_domain = new.split(".", 1)[0]
            if src_domain == new_domain:
                continue
            src_keys = _DOMAIN_SPECIFIC_SERVICE_DATA.get(src_domain)
            if not src_keys:
                continue
            tgt_keys = _DOMAIN_SPECIFIC_SERVICE_DATA.get(new_domain, frozenset())
            # Any source-only key the new domain doesn't share → refuse.
            offending = src_keys - tgt_keys
            if isinstance(data, dict) and any(k in offending for k in data):
                return True
            # HA also accepts service-data flat on the action node.
            if any(k in offending for k in node if k not in {"service", "action"}):
                return True
        for k, v in node.items():
            if k in _NON_ENTITY_PAYLOAD_KEYS:
                continue
            if _substitution_drops_required_service_data(v, substitutions):
                return True
    elif isinstance(node, list):
        for item in node:
            if _substitution_drops_required_service_data(item, substitutions):
                return True
    return False


def _ids_from_entity_field(value: Any) -> list[str]:
    """Extract entity_ids from a string (single or comma-separated),
    list, or anything else (→ empty)."""
    if isinstance(value, str):
        if "," in value:
            return [p.strip() for p in value.split(",") if p.strip()]
        return [value] if value else []
    if isinstance(value, list):
        return [x for x in value if isinstance(x, str)]
    return []


def _substitution_inverts_intent(node: Any, substitutions: dict[str, str]) -> bool:
    """Return True if applying ``substitutions`` would invert an
    action's semantic. The only case so far is an ``off``-verb action
    whose target moves to the ``scene`` domain — scenes only have an
    "activate" verb, so an off request would silently fire the scene."""
    if isinstance(node, dict):
        eids: list[str] = []
        eids.extend(_ids_from_entity_field(node.get("entity_id")))
        tgt = node.get("target")
        if isinstance(tgt, dict):
            eids.extend(_ids_from_entity_field(tgt.get("entity_id")))
        data = node.get("data")
        if isinstance(data, dict):
            eids.extend(_ids_from_entity_field(data.get("entity_id")))
        svc = node.get("service") or node.get("action")
        if isinstance(svc, str) and "." in svc:
            action = svc.split(".", 1)[1]
            verb = _SERVICE_ACTION_VERB.get(action)
            if verb == "off":
                for e in eids:
                    new = substitutions.get(e)
                    if new and new.split(".", 1)[0] == "scene":
                        return True
        for k, v in node.items():
            if k in _NON_ENTITY_PAYLOAD_KEYS:
                continue
            if _substitution_inverts_intent(v, substitutions):
                return True
    elif isinstance(node, list):
        for item in node:
            if _substitution_inverts_intent(item, substitutions):
                return True
    return False


def _bad_has_pinned_state_semantics(node: Any, bad_ids: set[str]) -> bool:
    """Return True if any node references one of ``bad_ids`` alongside
    a ``to:``, ``from:`` or ``state:`` key — i.e. the trigger/condition
    pins specific state values that don't carry across a domain swap."""
    if isinstance(node, dict):
        eids = _ids_from_entity_field(node.get("entity_id"))
        if any(e in bad_ids for e in eids) and any(k in node for k in ("to", "from", "state")):
            return True
        for k, v in node.items():
            if k in _NON_ENTITY_PAYLOAD_KEYS:
                continue
            if _bad_has_pinned_state_semantics(v, bad_ids):
                return True
    elif isinstance(node, list):
        for item in node:
            if _bad_has_pinned_state_semantics(item, bad_ids):
                return True
    return False


def _substitution_yields_mixed_domain_target(node: Any, substitutions: dict[str, str]) -> bool:
    """Return True if applying ``substitutions`` would produce an
    entity_id list (whether under ``target.entity_id``, the legacy
    ``data.entity_id``, or the flat ``entity_id`` at the action node)
    that spans more than one domain. The service-domain can only serve
    one domain at runtime, so the off-domain entries would silently
    fail."""

    def _list_is_mixed(value: Any) -> bool:
        # Normalise to a list of entity_ids — accept the canonical list
        # form AND HA's comma-separated string shorthand.
        ids = _ids_from_entity_field(value)
        if len(ids) < 2:
            return False
        if not any(x in substitutions for x in ids):
            return False
        patched = [substitutions.get(x, x) for x in ids]
        domains = {p.split(".", 1)[0] for p in patched if "." in p}
        return len(domains) > 1

    if isinstance(node, dict):
        if _list_is_mixed(node.get("entity_id")):
            return True
        tgt = node.get("target")
        if isinstance(tgt, dict) and _list_is_mixed(tgt.get("entity_id")):
            return True
        data = node.get("data")
        if isinstance(data, dict) and _list_is_mixed(data.get("entity_id")):
            return True
        for k, v in node.items():
            if k in _NON_ENTITY_PAYLOAD_KEYS:
                continue
            if _substitution_yields_mixed_domain_target(v, substitutions):
                return True
    elif isinstance(node, list):
        for item in node:
            if _substitution_yields_mixed_domain_target(item, substitutions):
                return True
    return False


def _apply_entity_substitutions(node: Any, substitutions: dict[str, str]) -> None:
    """Walk the automation tree applying entity_id substitutions; also swap
    the action service-domain prefix when the substituted target moves
    domain (so ``light.turn_on`` becomes ``switch.turn_on`` after the
    entity moves). Service swap is gated on an entity in this node
    actually being substituted — otherwise unrelated multi-target
    actions like ``homeassistant.update_entity`` targeting several
    sensors would be wrongly rewritten to ``sensor.update_entity``."""
    if isinstance(node, dict):
        node_substituted = False

        def _apply_field(container: dict[str, Any], key: str) -> tuple[bool, list[str]]:
            """Substitute ``container[key]``; handles string, comma-split
            string, and list shapes. Returns (changed, final_ids)."""
            value = container.get(key)
            if isinstance(value, str):
                if "," in value:
                    parts = [p.strip() for p in value.split(",")]
                    new_parts = [substitutions.get(p, p) for p in parts]
                    if new_parts != parts:
                        container[key] = ", ".join(new_parts)
                        return True, [p for p in new_parts if "." in p]
                    return False, [p for p in parts if "." in p]
                if value in substitutions:
                    container[key] = substitutions[value]
                    return True, [container[key]]
                return False, [value] if "." in value else []
            if isinstance(value, list):
                new_list = [substitutions.get(x, x) for x in value]
                if new_list != value:
                    container[key] = new_list
                    return True, [x for x in new_list if isinstance(x, str) and "." in x]
                return False, [x for x in value if isinstance(x, str) and "." in x]
            return False, []

        flat_changed, flat_ids = _apply_field(node, "entity_id")
        if flat_changed:
            node_substituted = True

        new_domain: str | None = None
        tgt = node.get("target")
        if isinstance(tgt, dict):
            tgt_changed, tgt_ids = _apply_field(tgt, "entity_id")
            if tgt_changed:
                node_substituted = True
                domains = {x.split(".", 1)[0] for x in tgt_ids}
                if len(domains) == 1:
                    new_domain = next(iter(domains))

        # Legacy ``data.entity_id`` shape — service call carries the
        # entity in ``data`` instead of ``target``. Validator still
        # accepts it, so we have to swap the service domain here too.
        data = node.get("data")
        if isinstance(data, dict):
            data_changed, data_ids = _apply_field(data, "entity_id")
            if data_changed:
                node_substituted = True
                if new_domain is None:
                    domains = {x.split(".", 1)[0] for x in data_ids}
                    if len(domains) == 1:
                        new_domain = next(iter(domains))

        if new_domain is None and node_substituted and flat_ids:
            domains = {x.split(".", 1)[0] for x in flat_ids}
            if len(domains) == 1:
                new_domain = next(iter(domains))

        svc = node.get("service") or node.get("action")
        svc_key = "service" if node.get("service") else "action"
        if isinstance(svc, str) and "." in svc and new_domain is not None and node_substituted:
            cur_domain, action = svc.split(".", 1)
            if cur_domain != new_domain:
                # Map by VERB across domains so e.g. ``lock.lock`` →
                # ``cover.close_cover`` (both express "off/close" intent)
                # instead of the invalid literal swap ``cover.lock``.
                verb = _SERVICE_ACTION_VERB.get(action)
                if verb is not None:
                    node[svc_key] = f"{new_domain}.{_service_verb_for_domain(verb, new_domain)}"
                else:
                    node[svc_key] = f"{new_domain}.{action}"

        for k, v in node.items():
            if k in _NON_ENTITY_PAYLOAD_KEYS:
                continue
            _apply_entity_substitutions(v, substitutions)
    elif isinstance(node, list):
        for item in node:
            _apply_entity_substitutions(item, substitutions)


def _humanise_unknown_entity_error(
    reason: str,
    entities: list[EntitySnapshot] | None,
) -> str:
    """Replace an "unknown entity_id" validator reason with a clarification
    that lists the user's actual devices grouped by domain (cap 3/domain so
    the chat bubble stays readable)."""
    if not entities or "unknown entity_id" not in reason:
        return (
            f"I couldn't create a valid automation from that request: {reason}. "
            "Please refine the request and try again."
        )

    by_domain: dict[str, list[str]] = {}
    # Domain → English plural label. Naive "+s" mishandles "switch" and
    # uncountable nouns like "climate", so map explicitly.
    domain_labels: dict[str, str] = {
        "light": "lights",
        "switch": "switches",
        "cover": "covers",
        "lock": "locks",
        "climate": "climate",
        "fan": "fans",
        "media_player": "media players",
        "vacuum": "vacuums",
        "scene": "scenes",
    }
    controllable_domains = tuple(domain_labels)
    for e in entities:
        eid = e.get("entity_id", "")
        if "." not in eid:
            continue
        domain = eid.split(".", 1)[0]
        if domain not in controllable_domains:
            continue
        fname = (e.get("attributes") or {}).get("friendly_name") or eid
        by_domain.setdefault(domain, []).append(str(fname))

    parts: list[str] = []
    for domain in controllable_domains:
        names = by_domain.get(domain)
        if not names:
            continue
        sample = ", ".join(names[:3])
        more = f" (+{len(names) - 3} more)" if len(names) > 3 else ""
        parts.append(f"{domain_labels[domain]}: {sample}{more}")

    if not parts:
        return (
            f"I couldn't create that automation — {reason}. "
            "Tell me which of your devices you'd like to use and when it should run."
        )

    summary = "; ".join(parts)
    return (
        f"I couldn't build that — {reason}. "
        f"Here's what I can actually control in your home — {summary}. "
        "Which device should the automation use, and when should it run?"
    )


# Coerce a state trigger to numeric_state when the prompt phrases a
# numeric comparator ("drops below 18", "warmer than 26"). Only state
# triggers on entities whose domain produces numeric measurements are
# eligible — coercing e.g. a binary_sensor.motion trigger would silently
# rewrite an unrelated trigger into a meaningless numeric threshold.
_NUMERIC_ELIGIBLE_DOMAINS: frozenset[str] = frozenset(
    {"sensor", "climate", "number", "input_number", "weather"}
)

_NUMERIC_THRESHOLD_RE = re.compile(
    r"\b(?:drops?|falls?|rises?|gets?|goes?)\s+"
    r"(?P<word>below|above|under|over|to)\s+(?P<n1>-?\d+(?:\.\d+)?)\b"
    r"|\b(?:warmer|hotter|higher)\s+than\s+(?P<n2>-?\d+(?:\.\d+)?)\b"
    r"|\b(?:cooler|colder|lower)\s+than\s+(?P<n3>-?\d+(?:\.\d+)?)\b"
    r"|\b(?P<word2>below|above|under|over)\s+(?P<n4>-?\d+(?:\.\d+)?)\b",
    re.IGNORECASE,
)


# Delimiters that end a trigger clause: commas, semicolons, the
# conjunction ``then``, or an unambiguous action-clause verb. The
# anchor-based clause extraction below restricts the imperative-verb
# split to the segment AFTER the trigger anchor (when/if/...), so
# leading-imperative prompts like "Turn on the heater when temperature
# drops below 18" still match ("below 18" sits inside the anchored
# tail "when temperature drops below 18"). Only verbs that are
# unambiguous action language in this position are listed — broad ones
# like "open"/"close"/"start"/"stop" double as state nouns and are
# omitted.
_TRIGGER_CLAUSE_SPLIT_RE = re.compile(
    r"[,;]|\bthen\b|\b(?:set|turn|send|notify|alert|activate)\b",
    re.IGNORECASE,
)

# Explicit trigger markers — when present, the trigger clause begins at
# the marker and extends to the next action-clause delimiter. This
# anchors the comparator search so "Set the thermostat below 18 when
# the outdoor temperature changes" can't attribute the action's
# "below 18" to the trigger.
_TRIGGER_ANCHOR_RE = re.compile(
    r"\b(?:when|if|once|while|whenever|as\s+soon\s+as)\b",
    re.IGNORECASE,
)


def _trigger_clause_of(user_message: str) -> str:
    """Return the substring of ``user_message`` that describes the
    trigger. When the prompt contains an explicit trigger marker
    (``when``, ``if``, ``once``, ``while``, ``whenever``, ``as soon as``)
    the clause spans from that marker to the next action-clause
    delimiter (comma/semicolon/``then``) or end of string. Otherwise
    the clause is the head of the message up to the first delimiter."""
    if not user_message:
        return ""
    anchor = _TRIGGER_ANCHOR_RE.search(user_message)
    if anchor is not None:
        tail = user_message[anchor.start() :]
        m = _TRIGGER_CLAUSE_SPLIT_RE.search(tail)
        return tail[: m.start()] if m else tail
    m = _TRIGGER_CLAUSE_SPLIT_RE.search(user_message)
    return user_message[: m.start()] if m else user_message


def _extract_numeric_threshold(user_message: str) -> tuple[str, float] | None:
    """Return ``("below"|"above", value)`` extracted from a comparator prompt, or None."""
    if not user_message:
        return None
    m = _NUMERIC_THRESHOLD_RE.search(user_message)
    if not m:
        return None
    n_str = m.group("n1") or m.group("n2") or m.group("n3") or m.group("n4")
    if n_str is None:
        return None
    try:
        n = float(n_str)
    except ValueError:
        return None
    word = (m.group("word") or m.group("word2") or "").lower()
    if m.group("n2") is not None:
        direction = "above"
    elif m.group("n3") is not None or word in ("below", "under"):
        direction = "below"
    elif word in ("above", "over"):
        direction = "above"
    elif word == "to":
        # "drops/falls to N" implies the value is falling toward N (below);
        # "rises to N" implies rising toward N (above). "goes/gets to N"
        # is ambiguous (reaching N from either side) — refuse to coerce.
        verb = m.group(0).split(None, 1)[0].lower()
        if verb in ("drops", "drop", "falls", "fall"):
            direction = "below"
        elif verb in ("rises", "rise"):
            direction = "above"
        else:
            return None
    else:
        direction = "below"
    return direction, n


def _read_triggers(automation: dict[str, Any]) -> list[Any]:
    """Return the automation's trigger payload as a list without
    mutating ``automation``. Singular ``trigger`` key wins over the
    plural ``triggers`` to match the validator's precedence — modifying
    the plural list when the validator reads the singular one would
    silently change which trigger is active after ``_commit_triggers``
    promotes the list and drops the singular key."""
    single_val = automation.get("trigger")
    if isinstance(single_val, list):
        return single_val
    if isinstance(single_val, dict):
        return [single_val]
    triggers_val = automation.get("triggers")
    if isinstance(triggers_val, list):
        return triggers_val
    if isinstance(triggers_val, dict):
        return [triggers_val]
    return []


def _commit_triggers(automation: dict[str, Any], triggers: list[Any]) -> None:
    """Write ``triggers`` back as the canonical ``triggers`` list and
    drop the legacy singular ``trigger`` key. The validator prioritizes
    the singular key when both are present, so mutating only the list
    would leave the LoRA's original (rejected) singular trigger in
    force despite a successful coercion."""
    automation["triggers"] = triggers
    automation.pop("trigger", None)


def _coerce_numeric_state_triggers(automation: dict[str, Any], user_message: str) -> bool:
    """Rewrite a state trigger to numeric_state when the prompt carries a
    numeric comparator. Only rewrites state-platform triggers whose
    entity_id domain produces numeric measurements (sensor/climate/...).
    Unrelated triggers (sun, time, binary_sensor state, …) are left
    untouched so a multi-trigger automation keeps its original semantics.

    Refuses to coerce when the prompt is ambiguous — i.e. when the prompt
    contains multiple distinct numeric comparators (each could belong to
    a different trigger), or when one comparator could plausibly target
    several eligible triggers. In those cases the validator's rejection
    bubbles up so the user can clarify.

    Mutates in place; returns True if anything changed."""
    # Restrict comparator search to the trigger clause — the head of the
    # prompt up to the first action-clause delimiter. Otherwise a
    # numeric value that lives in the action ("when the outdoor temp
    # changes, set the thermostat below 18") gets attached to the
    # trigger, changing when the automation fires.
    trigger_clause = _trigger_clause_of(user_message)
    extracted = _extract_numeric_threshold(trigger_clause)
    if extracted is None:
        return False
    # Bail out if the trigger clause names more than one distinct numeric
    # threshold — we can't know which trigger each one targets without
    # phrase-level alignment, and applying the first to every trigger
    # silently changes "temperature above 25 OR humidity below 40" into
    # "above 25 OR above 25".
    all_matches = list(_NUMERIC_THRESHOLD_RE.finditer(trigger_clause))
    distinct = {m.group(0).lower() for m in all_matches}
    if len(distinct) > 1:
        return False
    direction, value = extracted
    triggers = _read_triggers(automation)
    # If an existing numeric_state trigger already carries this exact
    # comparator (same direction + value), the prompt's threshold is
    # already attached to a trigger — applying it again to some other
    # state trigger would silently change its semantics. Refuse.
    for tr in triggers:
        if not isinstance(tr, dict):
            continue
        platform = tr.get("trigger") or tr.get("platform")
        if platform == "numeric_state" and tr.get(direction) == value:
            return False
    eligible: list[dict[str, Any]] = []
    for tr in triggers:
        if not isinstance(tr, dict):
            continue
        platform = tr.get("trigger") or tr.get("platform")
        if platform != "state":
            continue
        eid = tr.get("entity_id")
        if not isinstance(eid, str) or "." not in eid:
            continue
        domain = eid.split(".", 1)[0]
        if domain not in _NUMERIC_ELIGIBLE_DOMAINS:
            continue
        # climate.* and weather.* entities expose textual primary states
        # ("heat"/"off", "sunny"/"cloudy"); their numeric readings live
        # on attributes (``current_temperature``, ``humidity``, …). A
        # numeric_state trigger against the primary state would never
        # fire. Require an explicit ``attribute`` or refuse coercion —
        # we'd have to guess the attribute name otherwise, and a wrong
        # guess fails just as silently.
        if domain in ("climate", "weather") and not tr.get("attribute"):
            continue
        # A trigger that already names concrete ``to:`` / ``from:``
        # values is a deliberate state trigger, not a placeholder the
        # LoRA forgot to fill in. Coercing it to numeric_state would
        # silently change the fire condition — e.g. a thermostat
        # ``to: off`` trigger paired with an action clause ``set the
        # target below 18`` must NOT become ``below: 18``.
        if "to" in tr or "from" in tr:
            continue
        eligible.append(tr)
    # With exactly one comparator AND several eligible state triggers we
    # can't tell which trigger the comparator applies to — refuse rather
    # than rewrite all of them to the same threshold.
    if len(eligible) != 1:
        return False
    tr = eligible[0]
    eid = tr["entity_id"]
    # Preserve fields that carry meaning across the platform switch.
    # ``attribute`` is critical for entities like ``climate.thermostat``
    # whose primary state is a mode string ("heat"/"off"); the numeric
    # reading lives on ``current_temperature``. Dropping it would
    # silently make numeric_state evaluate the string state and never
    # fire. ``for:`` carries a debounce duration that applies to
    # numeric_state too.
    keep_for = tr.get("for")
    keep_attribute = tr.get("attribute")
    tr.clear()
    tr["trigger"] = "numeric_state"
    tr["entity_id"] = eid
    if keep_attribute is not None:
        tr["attribute"] = keep_attribute
    tr[direction] = value
    if keep_for is not None:
        tr["for"] = keep_for
    _commit_triggers(automation, triggers)
    return True


# Trigger-phrasing for a sun event: "at sunset", "every sunrise", "when
# the sun sets". Purely conditional phrasing ("…after sunset", "while
# it's dark") deliberately does NOT match — the sun word is then a guard
# on a separate trigger, not a fire-time, and synthesizing a sun trigger
# would make the automation fire unconditionally at sunset on top of
# whatever the user actually asked for.
_SUN_TRIGGER_LANG_RE = re.compile(
    r"\b(?:at|every|come|by|each|on)\s+(?:sunset|sundown|dusk|sunrise|sundawn|dawn)\b"
    r"|\bwhen\s+(?:the\s+)?sun\s+(?:sets?|rises?|comes?\s+up|goes?\s+down)\b",
    re.IGNORECASE,
)

# After a primary sun-trigger phrase, conjunctions ``and``/``or``/comma
# can chain bare event names that share the prefix. "at sunset and
# sunrise" requests both events; without this follow-on detector only
# the first event would be captured.
_SUN_TRIGGER_CONT_RE = re.compile(
    r"\s*(?:and|or|,)\s*"
    r"(?:sunset|sundown|dusk|sunrise|sundawn|dawn|"
    r"sun\s+sets?|sun\s+rises?|sun\s+comes?\s+up|sun\s+goes?\s+down)\b",
    re.IGNORECASE,
)

# Explicit clock-time phrase in the prompt (e.g. "at 10 PM", "at 22:00",
# "10 pm"). When the prompt contains BOTH a sun-trigger phrase AND an
# explicit time, the time trigger is intentional — append the sun
# trigger rather than replacing the time one.
_EXPLICIT_TIME_RE = re.compile(
    r"\b(?:\d{1,2}:\d{2}(?::\d{2})?|\d{1,2}\s*(?:am|pm|a\.m\.|p\.m\.))\b",
    re.IGNORECASE,
)


def _coerce_sun_triggers(automation: dict[str, Any], user_message: str) -> bool:
    """Ensure a sun trigger with the right event is present when the prompt
    *fires* on a sun event. Requires trigger phrasing ("at sunset",
    "every sunrise", "when the sun sets") — purely conditional phrases
    ("…after sunset") are ignored so motion+sun automations don't gain
    an unconditional fire-at-sunset trigger. Replaces a time-guess
    trigger (``time``/``time_pattern``) when one is present; otherwise
    appends. Mutates in place; returns True if anything changed."""
    if not user_message:
        return False
    msg = user_message.lower()
    # Collect every requested sun event in the prompt — "at sunrise and
    # at sunset" legitimately names both. Each match is independently
    # checked for trigger-vs-condition context using the nearest
    # preceding anchor:
    #
    # * If a trigger anchor (when/if/once/while/...) precedes THIS
    #   phrase without an intervening ``or``, the phrase is qualifying
    #   that earlier trigger as a condition (e.g. "if motion is
    #   detected at sunset", or the second clause of "at sunset turn
    #   light on, and turn it off if it is still on at sunrise"). Skip.
    # * Use the NEAREST preceding anchor so "when motion occurs or when
    #   the door opens at sunset" associates sunset with the second
    #   ``when`` clause rather than the first.
    wanted: set[str] = set()
    for match in _SUN_TRIGGER_LANG_RE.finditer(msg):
        head = msg[: match.start()]
        head_anchors = list(_TRIGGER_ANCHOR_RE.finditer(head))
        if head_anchors:
            anchor = head_anchors[-1]
            between = msg[anchor.end() : match.start()]
            if not re.search(r"\bor\b", between, re.IGNORECASE):
                continue
        phrase = match.group(0)
        # Greedily absorb any chained event names ("at sunset and
        # sunrise") so both events are recognised before the conflict
        # check downstream can delete one.
        end = match.end()
        while True:
            cont = _SUN_TRIGGER_CONT_RE.match(msg, end)
            if not cont:
                break
            phrase += msg[end : cont.end()]
            end = cont.end()
        if re.search(r"\b(sunset|sundown|dusk|sun\s+sets?|sun\s+goes?\s+down)\b", phrase):
            wanted.add("sunset")
        if re.search(r"\b(sunrise|sundawn|dawn|sun\s+rises?|sun\s+comes?\s+up)\b", phrase):
            wanted.add("sunrise")
    if not wanted:
        return False

    # Two distinct sun events paired with OPPOSING action verbs ("turn on
    # at sunset AND turn it off at sunrise") describe a multi-action
    # automation: each event drives a different action. Coercion can only
    # add triggers that share ALL actions, so appending the second event
    # would make the existing action (e.g. turn_on) fire at both events —
    # reversing the second half of the request. Leave it to the LLM.
    # Allow filler between the verb and on/off ("turn IT off", "turn the
    # porch light on") so both halves of a compound request are detected.
    has_on_verb = re.search(
        r"\b(?:turn|switch|power)\b[^.!?]{0,20}?\bon\b|\bopens?\b|\bopening\b",
        msg,
    )
    has_off_verb = re.search(
        r"\b(?:turn|switch|shut)\b[^.!?]{0,20}?\boff\b|\bcloses?\b|\bclosing\b",
        msg,
    )
    if len(wanted) >= 2 and has_on_verb and has_off_verb:
        return False

    triggers = _read_triggers(automation)
    present_events: set[str] = set()
    conflict_indices: list[int] = []
    for idx, tr in enumerate(triggers):
        if not isinstance(tr, dict):
            continue
        platform = tr.get("trigger") or tr.get("platform")
        if platform != "sun":
            continue
        ev = tr.get("event")
        if ev in wanted:
            present_events.add(ev)
        else:
            conflict_indices.append(idx)
    missing = wanted - present_events

    # Already correct AND no contradicting sun trigger → no-op.
    if not missing and not conflict_indices:
        return False

    changed = False
    # Removing or repurposing an existing sun trigger is only safe in a
    # narrow case: the prompt does NOT use additive language ("also",
    # "too", "in addition"), no requested event is already present, AND
    # exactly one existing sun trigger disagrees. That pattern fits the
    # "model emitted the wrong sun event" case (single stale trigger
    # to correct). In any other shape — additive language present, an
    # existing requested event also present, or multiple conflicts —
    # leave existing sun triggers alone and only synthesize missing
    # events: the conflict is most likely a deliberate carry-over from
    # an earlier refinement.
    is_additive = re.search(r"\b(?:also|too|as\s+well|in\s+addition|and\s+also)\b", msg) is not None
    can_repurpose = (
        not is_additive and not present_events and len(conflict_indices) == 1 and bool(missing)
    )
    if can_repurpose:
        idx = conflict_indices[0]
        ev = sorted(missing)[0]
        missing.discard(ev)
        triggers[idx] = {"trigger": "sun", "event": ev}
        changed = True

    if missing:
        # Repurpose a guessed time/time_pattern trigger when the prompt
        # has no explicit clock time. Each repurpose satisfies one
        # missing event.
        prompt_has_explicit_time = _EXPLICIT_TIME_RE.search(msg) is not None
        if not prompt_has_explicit_time:
            for idx, tr in enumerate(triggers):
                if not missing:
                    break
                if not isinstance(tr, dict):
                    continue
                platform = tr.get("trigger") or tr.get("platform")
                if platform in ("time", "time_pattern"):
                    ev = sorted(missing)[0]
                    missing.discard(ev)
                    triggers[idx] = {"trigger": "sun", "event": ev}
                    changed = True
        # Append any still-missing requested events — but guard against
        # widening a conditional automation. HA ORs triggers together, so
        # appending a sun trigger alongside a genuine non-time trigger
        # (motion/state/numeric_state) for "At sunset, turn on the lights
        # IF motion is detected" would fire on motion at ANY time too,
        # widening the automation past the requested window. The sun
        # phrase there qualifies the action, not a second trigger.
        #
        # Exception: explicit OR phrasing ("at sunset OR when motion is
        # detected") genuinely makes them alternative triggers — appending
        # is correct there. So block the append only when another primary
        # trigger is present AND the prompt has no "or" joining them.
        if missing:
            has_other_primary = any(
                isinstance(tr, dict)
                and (tr.get("trigger") or tr.get("platform")) not in ("time", "time_pattern", "sun")
                for tr in triggers
            )
            has_or = re.search(r"\bor\b", msg) is not None
            if not has_other_primary or has_or:
                for ev in sorted(missing):
                    triggers.append({"trigger": "sun", "event": ev})
                    changed = True

    if not changed:
        return False
    _commit_triggers(automation, triggers)
    return True


# Presence + duration phrasing — split into negative ("nobody is here for
# N minutes") and positive ("someone is here for N minutes") regexes so
# the synthesized trigger can pick the right ``to:`` state. Lumping both
# under one regex and always emitting ``to: off`` inverted positive
# prompts (fired on absence rather than presence).
_PRESENCE_NEGATIVE_FOR_DURATION_RE = re.compile(
    r"\b(?P<word>nobody|no\s*one|noone)\b"
    r"(?:[^.!?]{0,40}?)"
    r"\bfor\s+(\d+)\s+(second|minute|hour)s?\b",
    re.IGNORECASE,
)
_PRESENCE_POSITIVE_FOR_DURATION_RE = re.compile(
    # ``someone``/``anyone``/``anybody`` mean "at least one member" —
    # maps cleanly to a person-group going to ``home``. ``everyone`` is
    # semantically different ("all members present") and is handled by
    # ``_PRESENCE_EVERYONE_FOR_DURATION_RE`` below so the coercion can
    # refuse rather than synthesize a misleading trigger.
    r"\b(?P<word>someone|anyone|anybody)\b"
    r"(?:[^.!?]{0,40}?)"
    r"\bfor\s+(\d+)\s+(second|minute|hour)s?\b",
    re.IGNORECASE,
)
# "everyone is home for 10 minutes" requires ALL members present, but a
# standard person-group reports ``home`` when ANY member is home (HA
# group default: ``all: false``). Without a guaranteed all-members
# aggregate the trigger would fire as soon as one resident arrives.
# Detected separately so the coercion can refuse.
_PRESENCE_EVERYONE_FOR_DURATION_RE = re.compile(
    r"\b(?P<word>everyone|everybody)\b"
    r"(?:[^.!?]{0,40}?)"
    r"\bfor\s+(\d+)\s+(second|minute|hour)s?\b",
    re.IGNORECASE,
)
_ROOM_IS_EMPTY_RE = re.compile(
    r"\b(?:the\s+)?(?P<room>kitchen|bedroom|bathroom|office|garage|porch|"
    r"hallway|basement|attic|living\s*room|family\s*room|dining\s*room)\b"
    r"\s+(?:is\s+empty|are\s+empty|stays?\s+empty)\b"
    r"(?:[^.!?]{0,40}?)"
    r"\bfor\s+(?P<n>\d+)\s+(?P<unit>second|minute|hour)s?\b",
    re.IGNORECASE,
)


def _match_presence_for_duration(msg: str) -> tuple[re.Match[str], bool] | None:
    """Return (match, is_positive) for the first presence + duration
    phrase in ``msg``. ``is_positive`` is True for affirmative phrasings
    (someone/anyone/anybody) and False for negative ones
    (nobody/no one/noone) plus "the X is empty" variants. ``everyone``
    is handled separately by ``_is_everyone_presence_prompt`` since its
    "all members present" semantics can't be expressed against a
    standard person-group. Returns None when no presence + duration
    phrase is found."""
    if not msg:
        return None
    m = _PRESENCE_NEGATIVE_FOR_DURATION_RE.search(msg)
    if m is not None:
        return m, False
    m = _ROOM_IS_EMPTY_RE.search(msg)
    if m is not None:
        return m, False
    m = _PRESENCE_POSITIVE_FOR_DURATION_RE.search(msg)
    if m is not None:
        return m, True
    return None


def _is_everyone_presence_prompt(msg: str) -> bool:
    """True when the prompt's presence + duration condition uses
    ``everyone``/``everybody`` ("everyone is home for 10 minutes"). The
    coercion refuses these to avoid synthesizing a person-group
    ``to: home`` trigger that fires when only one resident arrives."""
    return bool(_PRESENCE_EVERYONE_FOR_DURATION_RE.search(msg or ""))


def _has_presence_for_duration(msg: str) -> bool:
    if not msg:
        return False
    return _match_presence_for_duration(msg) is not None or _is_everyone_presence_prompt(msg)


def _first_action_target_entity_id(
    automation: dict[str, Any],
    hass: HomeAssistant | None,
) -> str | None:
    """Return the first action-target ``entity_id`` that exists in HA.

    Used as the last-ditch fallback for ``_coerce_presence_for_duration_trigger``
    when the home has no presence/occupancy/motion/person entity — better
    to synthesize a state-trigger against the action target (so HA actually
    creates a valid automation with a ``for:`` duration matching the prompt)
    than to dead-end on the validator's "no trigger" rejection.
    """
    if hass is None:
        return None

    def _walk(node: Any) -> list[str]:
        eids: list[str] = []
        if isinstance(node, dict):
            for k, v in node.items():
                if k == "entity_id":
                    if isinstance(v, str):
                        eids.append(v)
                    elif isinstance(v, list):
                        eids.extend(x for x in v if isinstance(x, str))
                else:
                    eids.extend(_walk(v))
        elif isinstance(node, list):
            for item in node:
                eids.extend(_walk(item))
        return eids

    actions = automation.get("actions") or automation.get("action") or []
    for eid in _walk(actions):
        if "." in eid and hass.states.get(eid) is not None:
            return eid
    return None


_PRESENCE_ROOM_WORDS: tuple[str, ...] = (
    "living room",
    "family room",
    "dining room",
    "laundry room",
    "front porch",
    "back porch",
    "kitchen",
    "bedroom",
    "bathroom",
    "office",
    "garage",
    "porch",
    "hallway",
    "basement",
    "attic",
)


def _condition_clause(user_message: str) -> str:
    """Return the conditional clause of a presence + duration prompt.

    "turn off the kitchen lights when nobody is home for 10 minutes"
    has two distinct rooms in play: ``kitchen`` is the ACTION target,
    ``nobody is home`` is the PRESENCE condition (no room). Scanning
    the whole prompt for a room word would pick up ``kitchen`` and
    synthesize a kitchen-occupancy trigger, firing when the kitchen is
    empty even while residents remain home — the opposite of the
    intended household-wide condition.

    Split at the first conditional connector (``when``/``whenever``/
    ``while``/``if``) and return everything after it. Without a
    connector the message is treated as the condition itself
    (presence + duration phrasing can stand alone).
    """
    if not user_message:
        return ""
    msg = user_message.lower()
    # Word-boundary match so a connector at the START of the message
    # ("When nobody is home for 10 minutes, turn off the kitchen lights")
    # is split too — a leading-space search would miss it and the room
    # scan would then see ``kitchen`` in the trailing action clause.
    best: int | None = None
    for connector in ("whenever", "while", "when", "if"):
        m = re.search(rf"\b{connector}\b", msg)
        if m is not None and (best is None or m.end() < best):
            best = m.end()
    if best is None:
        return msg
    clause = msg[best:]
    # A leading-connector form puts the ACTION after the condition.
    # Truncate so the action-target room ("kitchen" in "...turn off the
    # kitchen lights") doesn't leak into the presence-room scan:
    #   * at a comma / "then" separator, and
    #   * at the END of the presence duration phrase ("for N <unit>s") —
    #     the condition ends there, so an unpunctuated leading form
    #     ("When nobody is home for 10 minutes turn off the kitchen
    #     lights") is still bounded.
    cut = len(clause)
    for sep in (",", " then "):
        idx = clause.find(sep)
        if idx >= 0:
            cut = min(cut, idx)
    dur = re.search(r"\bfor\s+\d+\s+(?:second|minute|hour)s?\b", clause)
    if dur is not None:
        cut = min(cut, dur.end())
    return clause[:cut]


# binary_sensor device_classes / keyword markers that indicate a
# presence-class sensor. Shared by the room-vocabulary derivation and
# the entity scorer.
_PRESENCE_CLASS_KEYWORDS = ("occupancy", "presence", "motion")
_PRESENCE_DEVICE_CLASSES = frozenset({"occupancy", "presence", "motion", "moving"})


def _is_presence_class_sensor(slug: str, fname: str, device_class: str) -> bool:
    if device_class in _PRESENCE_DEVICE_CLASSES:
        return True
    return any(kw in slug or kw in fname for kw in _PRESENCE_CLASS_KEYWORDS)


def _live_room_vocabulary(hass: HomeAssistant | None) -> list[str]:
    """Derive room name candidates from the live presence-class entities.

    The static ``_PRESENCE_ROOM_WORDS`` list can't enumerate user-defined
    areas ("conservatory", "snug", "mancave"). Strip the presence-class
    suffix from each presence sensor's slug to recover its room stem
    (``binary_sensor.conservatory_occupancy`` → ``conservatory``) so a
    prompt naming that room resolves to the right sensor instead of
    falling back to household-wide presence."""
    if hass is None:
        return []
    rooms: set[str] = set()
    for state in hass.states.async_all():
        eid = state.entity_id
        if not eid.startswith("binary_sensor."):
            continue
        slug = eid.split(".", 1)[1].lower()
        fname = (state.attributes.get("friendly_name") or "").lower()
        device_class = str(state.attributes.get("device_class") or "").lower()
        if not _is_presence_class_sensor(slug, fname, device_class):
            continue
        stem = slug
        for kw in _PRESENCE_CLASS_KEYWORDS:
            stem = stem.replace(f"_{kw}", "").replace(f"{kw}_", "")
        stem = stem.strip("_").replace("_", " ").strip()
        if len(stem) >= 3:
            rooms.add(stem)
    return list(rooms)


def _target_room_from_prompt(
    user_message: str,
    hass: HomeAssistant | None = None,
) -> str | None:
    """Return the first room word that appears in the CONDITION clause
    of ``user_message``, or None. Longer phrases are checked first so
    "living room" wins over the embedded "room" / "porch" substring.

    Matches against the static room list PLUS room stems derived from
    the live presence sensors (see ``_live_room_vocabulary``) so a
    user-defined area like "conservatory" still resolves.

    Restricts the search to the conditional clause (see
    ``_condition_clause``) so action-target rooms don't leak into
    presence-room selection. "turn off the kitchen lights when nobody
    is home for 10 minutes" therefore returns None (whole-home
    presence), not ``kitchen``."""
    if not user_message:
        return None
    scope = _condition_clause(user_message)
    if not scope:
        return None
    vocab = set(_PRESENCE_ROOM_WORDS) | set(_live_room_vocabulary(hass))
    for room in sorted(vocab, key=lambda r: -len(r)):
        if re.search(rf"\b{re.escape(room)}\b", scope):
            return room
    return None


def _find_presence_entity(hass: HomeAssistant | None, user_message: str) -> str | None:
    """Pick the best presence entity for the room named in ``user_message``.
    Prefer occupancy / presence binary_sensors; fall back to motion
    sensors; then any device_tracker / person / group.

    When the prompt names a specific room, only entities whose slug or
    friendly_name matches that room are eligible — returning a sensor
    from the wrong room would silently rewire the user's automation to
    a different physical space ("kitchen" prompt picking up
    ``binary_sensor.bedroom_occupancy``). In that case return None so
    the caller can clarify instead of guessing.

    When the prompt names no room ("when nobody is home for 10 minutes"),
    the user is asking about the whole household — pick a household
    group entity (``group.all_persons`` / ``group.family`` /
    ``person.home`` aggregate) over any individual ``person.*`` or
    ``device_tracker.*``. An individual person's state going to
    ``not_home`` only means THAT person left; the automation would fire
    while other residents are still home, the opposite of what "nobody
    is home" means. Refuse (return None) when no aggregate source
    exists so the caller surfaces a clarification."""
    if hass is None or not user_message:
        return None
    target_room = _target_room_from_prompt(user_message, hass)
    room_slug = target_room.replace(" ", "_") if target_room else None

    def _is_person_group(state: Any) -> bool:
        """True when ``state`` is a ``group.*`` whose members are all
        ``person.*`` / ``device_tracker.*``. A generic group such as
        ``group.all_lights`` reports ``on``/``off`` and never reaches
        ``not_home`` — selecting it would synthesize a trigger that
        never fires."""
        if not state.entity_id.startswith("group."):
            return False
        members = state.attributes.get("entity_id")
        if not isinstance(members, (list, tuple)) or not members:
            return False
        return all(
            isinstance(m, str) and m.startswith(("person.", "device_tracker.")) for m in members
        )

    candidates: list[tuple[int, str]] = []
    for state in hass.states.async_all():
        eid = state.entity_id
        if not eid.startswith(("binary_sensor.", "device_tracker.", "person.", "group.")):
            continue
        # Skip unavailable/unknown sensors — a trigger on one validates
        # (the entity exists) but never fires until the sensor recovers.
        # Mirrors the normal entity collector's availability filter so a
        # live lower-ranked motion sensor wins over a dead occupancy one.
        if str(getattr(state, "state", "")).lower() in ("unavailable", "unknown"):
            continue
        slug = eid.split(".", 1)[1]
        fname = (state.attributes.get("friendly_name") or "").lower()
        device_class = str(state.attributes.get("device_class") or "").lower()
        room_match = bool(
            room_slug and (room_slug in slug or (target_room and target_room in fname))
        )
        # Room-named prompts MUST resolve to a room-matching entity.
        if target_room is not None and not room_match:
            continue
        # ANY ``group.*`` candidate must contain person/device_tracker
        # members — regardless of whether a room was named. A light group
        # ``group.kitchen_lights`` reports ``on``/``off``, never
        # ``home``/``not_home``, so a synthesized presence trigger on it
        # would never fire. This applies even to room-matching groups.
        if eid.startswith("group.") and not _is_person_group(state):
            continue
        # Room-less prompts ("when nobody is home for 10 minutes") only
        # accept household-aggregate presence sources. Excluding
        # individual ``person.*`` / ``device_tracker.*`` avoids firing
        # when ONE resident leaves while others remain home.
        if target_room is None and not eid.startswith("group."):
            continue

        # A room-matching ``binary_sensor.*`` must ALSO be a presence
        # class — otherwise ``binary_sensor.kitchen_window`` would be
        # picked for "kitchen" and ``to: off`` would mean "window
        # closed", not "nobody home". person/device_tracker/group are
        # inherently presence sources and skip this gate.
        if eid.startswith("binary_sensor.") and not _is_presence_class_sensor(
            slug, fname, device_class
        ):
            continue

        score = 0
        if "occupancy" in slug or "occupancy" in fname or device_class == "occupancy":
            score += 100
        if "presence" in slug or "presence" in fname or device_class == "presence":
            score += 100
        if "motion" in slug or "motion" in fname or device_class in {"motion", "moving"}:
            score += 50
        if eid.startswith("group."):
            score += 20
        if room_match:
            score += 200
        if score:
            candidates.append((score, eid))

    if not candidates:
        return None
    candidates.sort(key=lambda x: -x[0])
    return candidates[0][1]


_PROMPT_CLOCK_TIME_RE = re.compile(
    r"\b(?P<h>\d{1,2}):(?P<m>\d{2})(?::\d{2})?\s*(?P<ap1>am|pm|a\.m\.|p\.m\.)?"
    r"|\b(?P<h2>\d{1,2})\s*(?P<ap2>am|pm|a\.m\.|p\.m\.)",
    re.IGNORECASE,
)


def _prompt_explicit_times(msg: str) -> set[tuple[int, int]]:
    """Return the set of (hour24, minute) clock times the prompt names
    explicitly ("At 10:00" → {(10, 0)}, "at 7pm" → {(19, 0)}). Used to
    spare a genuine time trigger from the duration-misread filter."""
    times: set[tuple[int, int]] = set()
    if not msg:
        return times
    for m in _PROMPT_CLOCK_TIME_RE.finditer(msg):
        if m.group("h") is not None:
            hh = int(m.group("h"))
            mm = int(m.group("m"))
            ap = (m.group("ap1") or "").lower()
        else:
            hh = int(m.group("h2"))
            mm = 0
            ap = (m.group("ap2") or "").lower()
        if ap.startswith("p") and hh < 12:
            hh += 12
        elif ap.startswith("a") and hh == 12:
            hh = 0
        if 0 <= hh <= 23 and 0 <= mm <= 59:
            times.add((hh, mm))
    return times


def _trigger_time_in(trigger: dict[str, Any], times: set[tuple[int, int]]) -> bool:
    """True when ``trigger`` is a time trigger whose ``at`` (hour, minute)
    is one the prompt explicitly named — i.e. a genuine user time, not a
    duration misread."""
    if not times:
        return False
    platform = trigger.get("trigger") or trigger.get("platform")
    if platform != "time":
        return False
    at = trigger.get("at")
    if not isinstance(at, str):
        return False
    parts = at.split(":")
    if len(parts) < 2:
        return False
    try:
        return (int(parts[0]), int(parts[1])) in times
    except ValueError:
        return False


def _coerce_presence_for_duration_trigger(
    automation: dict[str, Any],
    user_message: str,
    hass: HomeAssistant | None,
) -> bool:
    """Append a presence state trigger when the prompt is presence + duration
    phrased.

    The v0.4.7 automation LoRA misreads presence prompts two ways:

      (a) Routes through command (no automation envelope at all) — handled
          upstream by the new ``_AUTOMATION_PATTERNS`` anchor in intent.py.
      (b) Emits ``{"trigger": "time", "at": "N:00:00"}`` — interprets the
          duration as a clock time. This coercion catches (b): append a
          state trigger with the requested ``for:`` window so HA actually
          fires after the (in)activity period, and remove ONLY the LoRA
          time trigger that encodes the duration value as a clock time.
          Any other triggers and all conditions are preserved — compound
          prompts ("…for 10 minutes or at sunset", "…when nobody is home
          for 10 minutes and the door is unlocked") keep their non-
          presence shape intact.

    Affirmative phrasings ("someone is here for 10 minutes") emit
    ``to: on``/``home``; negative phrasings ("nobody is in the kitchen
    for 10 minutes", "the kitchen is empty for 10 minutes") emit
    ``to: off``/``not_home``. Refuses (returns False) when no presence
    entity matches the named room — the caller surfaces a clarification
    rather than synthesizing a trigger on an unrelated sensor or on
    the controlled device.

    Returns ``True`` if a trigger was appended, ``False`` otherwise.
    """
    msg = user_message or ""
    # "everyone is home for 10 minutes" requires ALL members present,
    # which a default HA person-group (state == "home" when ANY member
    # is home) cannot express. Refuse rather than synthesize a trigger
    # that fires when the first resident arrives.
    if _is_everyone_presence_prompt(msg):
        return False
    matched = _match_presence_for_duration(msg)
    if matched is None:
        return False
    m, is_positive = matched
    # ``_ROOM_IS_EMPTY_RE`` uses named groups; the presence-word regexes
    # use positional groups for the count + unit.
    try:
        amount = int(m.group("n"))
        unit = m.group("unit").lower()
    except LookupError:
        amount = int(m.group(2))
        unit = m.group(3).lower()

    presence_eid = _find_presence_entity(hass, msg)
    if not presence_eid:
        # No room-matching presence entity — refuse rather than
        # synthesize a trigger on the controlled device or a sensor in
        # another room. The caller turns this into a clarification.
        return False

    duration_kw = {f"{unit}s": amount}
    if presence_eid.startswith(("person.", "device_tracker.", "group.")):
        # ``group.*`` of person/device_tracker members reports
        # ``home``/``not_home`` like its members; binary_sensor groups
        # report ``on``/``off`` — leave those to the else branch below.
        state_value = "home" if is_positive else "not_home"
    else:
        state_value = "on" if is_positive else "off"

    # Clock times the prompt EXPLICITLY names ("At 10:00, …", "at 7pm").
    # A time trigger whose ``at`` matches one of these is genuine — the
    # user asked for it — even if it also coincides with the duration
    # misread shape ("for 10 minutes" → "10:00:00"). Keep those.
    prompt_times = _prompt_explicit_times(msg)

    # Normalize across the two HA-accepted shapes (``trigger:`` singular,
    # ``triggers:`` plural). Keep existing non-presence triggers — they
    # may be genuine compound shape ("…or at sunset"). Drop ONLY the
    # specific time trigger that looks like the LoRA's "for N min" →
    # "at N:00:00" misread, UNLESS the prompt explicitly names that time.
    existing = _read_triggers(automation)
    preserved: list[dict[str, Any]] = []
    for tr in existing:
        if not isinstance(tr, dict):
            continue
        platform = tr.get("trigger") or tr.get("platform")
        # Drop ANY time/time_pattern trigger the prompt did not explicitly
        # name. This covers both the duration misread ("for 10 minutes" →
        # ``at: 10:00``) and a stray hallucinated clock trigger (a random
        # ``time`` at ``12:00`` on a "nobody home for 10 minutes" prompt).
        # Keeping such a trigger would demote presence to a condition and
        # produce "at noon if nobody is home" instead of firing after the
        # home has been empty. Explicit prompt times ("At 10:00 …") are in
        # ``prompt_times`` and survive.
        if platform in ("time", "time_pattern") and not _trigger_time_in(tr, prompt_times):
            continue
        # Drop any prior presence state trigger on the SAME entity to
        # avoid duplicate-fire on validator reload.
        if platform == "state" and tr.get("entity_id") == presence_eid:
            continue
        preserved.append(tr)

    has_or = re.search(r"\bor\b", msg, re.IGNORECASE) is not None

    # No primary trigger survived, but the prompt explicitly names a
    # clock time ("At 10:00, turn off the lights if nobody is home for 10
    # minutes") — that time IS the primary trigger and the model just
    # dropped it. Synthesize it from the prompt so the presence stays a
    # GATE (condition) instead of becoming the trigger, which would fire
    # the automation whenever the home empties, ignoring the schedule.
    if not preserved and not has_or and prompt_times:
        for hh, mm in sorted(prompt_times):
            preserved.append({"trigger": "time", "at": f"{hh:02d}:{mm:02d}:00"})

    # Presence-as-condition: when a presence clause sits alongside a
    # genuine PRIMARY trigger ("At sunset, turn on the lights if nobody
    # is home for 10 minutes"; "At 10:00, turn off the lights when nobody
    # is home for 10 minutes"), the presence is a GATE on that trigger,
    # not its own trigger. Appending it as a trigger would make HA OR the
    # two, firing the moment the home empties regardless of the primary
    # trigger's time. Emit it as a state condition with the same ``for:``
    # window instead.
    #
    # Applies whenever a genuine trigger survives filtering (``preserved``
    # non-empty) — BOTH "if" and "when" introduce the gate. The sole
    # exception is explicit OR phrasing ("...for 10 minutes OR at
    # sunset"), which genuinely makes presence an alternative trigger.
    if preserved and not has_or:
        new_condition: dict[str, Any] = {
            "condition": "state",
            "entity_id": presence_eid,
            "state": state_value,
            "for": duration_kw,
        }
        _commit_triggers(automation, preserved)
        _append_condition(automation, new_condition)
        return True

    new_trigger: dict[str, Any] = {
        "trigger": "state",
        "entity_id": presence_eid,
        "to": state_value,
        "for": duration_kw,
    }
    preserved.append(new_trigger)
    _commit_triggers(automation, preserved)
    return True


def _append_condition(automation: dict[str, Any], condition: dict[str, Any]) -> None:
    """Append ``condition`` to the automation, normalizing the singular
    ``condition`` / plural ``conditions`` shapes into a single
    ``conditions`` list (mirrors ``_commit_triggers``)."""
    existing: list[Any] = []
    single = automation.get("condition")
    if isinstance(single, list):
        existing = list(single)
    elif isinstance(single, dict):
        existing = [single]
    else:
        plural = automation.get("conditions")
        if isinstance(plural, list):
            existing = list(plural)
        elif isinstance(plural, dict):
            existing = [plural]
    existing.append(condition)
    automation["conditions"] = existing
    automation.pop("condition", None)


# Verb → domain preferences for synthesizing an action when the LoRA
# emitted an automation envelope so empty/truncated that no action
# survived parsing. Order inside the domain tuple matters: the entity
# scorer prefers earlier entries when prompt-token overlap ties.
_PROMPT_VERB_TO_ACTION_DOMAINS: tuple[tuple[re.Pattern[str], tuple[str, ...], str], ...] = (
    (
        re.compile(r"\bturn\s+off\b|\bswitch\s+off\b|\bshut\s+off\b", re.IGNORECASE),
        ("light", "switch", "fan", "media_player", "input_boolean", "climate"),
        "off",
    ),
    (
        re.compile(r"\bturn\s+on\b|\bswitch\s+on\b|\bpower\s+on\b", re.IGNORECASE),
        ("light", "switch", "fan", "media_player", "input_boolean", "climate"),
        "on",
    ),
    (
        re.compile(r"\bunlock\b", re.IGNORECASE),
        ("lock",),
        "on",
    ),
    (
        re.compile(r"\block\b", re.IGNORECASE),
        ("lock", "cover"),
        "off",
    ),
    (
        re.compile(r"\bclose\b", re.IGNORECASE),
        ("cover",),
        "off",
    ),
    (
        re.compile(r"\bopen\b", re.IGNORECASE),
        ("cover",),
        "on",
    ),
)


def _synthesize_action_from_prompt(
    hass: HomeAssistant | None,
    user_message: str,
) -> dict[str, Any] | None:
    """Build a single action dict from the prompt's verb + named target.

    Used as a last-ditch fallback when the LoRA emitted a truncated
    automation envelope with no actions at all — typically the presence
    + duration phrasing under context pressure, e.g. the LoRA running
    out of tokens after ``{"intent":"automation","response":"Turns the
    porch light off, and"}``. Without an action the trigger-recovery
    branch's re-validation fails on "must include at least one action"
    and the whole envelope gets discarded.

    Returns ``None`` when hass is missing, the prompt has no recognised
    verb, or no live entity has any token overlap with the prompt
    within the verb's preferred domains.
    """
    if hass is None or not user_message:
        return None
    domains_preferred: tuple[str, ...] = ()
    verb: str | None = None
    for pattern, domains, v in _PROMPT_VERB_TO_ACTION_DOMAINS:
        if pattern.search(user_message):
            domains_preferred = domains
            verb = v
            break
    if verb is None:
        return None
    raw_tokens = {t for t in re.split(r"[^a-z0-9]+", user_message.lower()) if t}
    prompt_tokens = {t for t in raw_tokens if len(t) >= 3 and t not in _PROMPT_KEYWORD_STOPWORDS}
    if not prompt_tokens:
        return None
    # Rank by (-score, domain_rank). The eid is NOT a tie-breaker: when
    # two entities tie on both score and domain rank ("Front Porch Light"
    # vs "Back Porch Light" for "turn off the porch light"), the target
    # is genuinely ambiguous — synthesizing for an arbitrary one ships a
    # valid automation for the wrong device. Refuse so the caller asks.
    scored: list[tuple[int, int, str]] = []  # (-score, domain_rank, eid)
    for state in hass.states.async_all():
        eid = state.entity_id
        if "." not in eid:
            continue
        domain, slug = eid.split(".", 1)
        if domain not in domains_preferred:
            continue
        slug_tokens = {t for t in re.split(r"[^a-z0-9]+", slug.lower()) if t}
        fname = (state.attributes.get("friendly_name") or "").lower()
        fname_tokens = {t for t in re.split(r"[^a-z0-9]+", fname) if t}
        overlap = (slug_tokens | fname_tokens) & prompt_tokens
        if not overlap:
            continue
        score = len(overlap)
        domain_rank = domains_preferred.index(domain)
        scored.append((-score, domain_rank, eid))
    if not scored:
        return None
    scored.sort()
    top_rank = (scored[0][0], scored[0][1])
    if sum(1 for s in scored if (s[0], s[1]) == top_rank) > 1:
        # Tie on score + domain rank → ambiguous target. Refuse.
        return None
    target_eid = scored[0][2]
    target_domain = target_eid.split(".", 1)[0]
    action_verb = _service_verb_for_domain(verb, target_domain)
    return {
        "service": f"{target_domain}.{action_verb}",
        "target": {"entity_id": target_eid},
    }


def _apply_prompt_aware_coercions(
    automation: dict[str, Any],
    user_message: str,
    hass: HomeAssistant,
    entities: list[EntitySnapshot] | None = None,
) -> dict[str, Any] | None:
    """Apply prompt-aware trigger coercions and re-validate. Returns
    the normalized automation on success, None if no coercion was
    needed OR the coerced shape still fails validation.

    For presence + duration prompts whose LoRA output is so truncated
    that no action survived ("Turns the porch light off, and"), a single
    action is synthesized from the prompt verb + named target so the
    trigger-recovery path doesn't dead-end on the validator's "must
    include at least one action" check.

    When ``entities`` is provided and post-coercion validation still
    fails for ``unknown entity_id`` (the LoRA's action targets a name
    that doesn't exist in the live fixture, e.g. ``light.porch`` when
    only ``input_boolean.porch_light`` is registered), an aggressive
    substitution pass is run on the coerced candidate — the prompt's
    presence + duration shape is enough signal to prefer ANY
    controllable entity sharing a prompt token over dead-ending."""
    if not isinstance(automation, dict):
        return None

    candidate = copy.deepcopy(automation)
    changed = False
    # Presence + duration phrasing is the only shape where a half-
    # truncated LoRA envelope is recoverable — gating action synthesis
    # on the same regex keeps this from misfiring on unrelated
    # automation prompts whose missing-action is a real user error.
    has_presence_duration = _has_presence_for_duration(user_message or "")
    if has_presence_duration:
        existing_actions = candidate.get("actions") or candidate.get("action") or []
        if not existing_actions:
            synthesized = _synthesize_action_from_prompt(hass, user_message)
            if synthesized is not None:
                candidate["actions"] = [synthesized]
                candidate.pop("action", None)
                changed = True
    if _coerce_sun_triggers(candidate, user_message):
        changed = True
    if _coerce_numeric_state_triggers(candidate, user_message):
        changed = True
    if _coerce_presence_for_duration_trigger(candidate, user_message, hass):
        changed = True
    if not changed:
        return None
    is_valid, reason, normalized = validate_automation_payload(candidate, hass)
    if is_valid and normalized is not None:
        return normalized
    # Post-coercion recovery — when the LoRA's action targets an entity
    # that doesn't exist in this fixture (typical for ``light.porch``
    # vs the only-real ``input_boolean.porch_light``), substitution
    # didn't run earlier because the original validator reason was
    # "must include at least one trigger" (not "unknown entity_id").
    # Now that the trigger is synthesized, the action-side
    # ``unknown entity_id`` surfaces — fix it here so the whole
    # envelope can ship.
    if reason and "unknown entity_id" in reason and entities:
        patched, subs = _resolve_unknown_entity_ids(
            reason,
            candidate,
            entities,
            hass,
            user_message=user_message,
            aggressive=True,
        )
        if patched is not None and subs:
            is_valid2, _r2, normalized2 = validate_automation_payload(patched, hass)
            if is_valid2 and normalized2 is not None:
                _LOGGER.info(
                    "Post-coercion entity substitution rescued %d id(s): %s",
                    len(subs),
                    subs,
                )
                return normalized2
    return None


def parse_architect_response(
    text: str,
    hass: HomeAssistant,
    entities: list[EntitySnapshot] | None = None,
    user_message: str | None = None,
    *,
    language: str | None = None,
) -> ArchitectResponse:
    """Parse the JSON response from the architect LLM. Normalises the result
    to always include 'intent' and 'response'; for 'automation' intent
    generates automation_yaml server-side. Strips
    ``suppressed_duplicate_command`` so a prompt-injected payload cannot
    bypass ``apply_command_policy``. When ``entities`` are provided an
    unknown-entity rejection is humanised + auto-corrected; when
    ``user_message`` is provided trigger coercions are applied."""
    try:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1:
            return {"intent": "answer", "response": text}

        data: dict[str, Any] = json.loads(text[start : end + 1])
        data.pop("suppressed_duplicate_command", None)

        if "intent" not in data:
            if "automation" in data:
                data["intent"] = "automation"
            elif "scene" in data:
                data["intent"] = "scene"
            elif "calls" in data:
                data["intent"] = "command"
            else:
                data["intent"] = "answer"

        if "scene" in data:
            from ..scene_utils import validate_scene_payload

            is_valid, reason, normalized = validate_scene_payload(data["scene"], hass)
            if not is_valid or normalized is None:
                _LOGGER.warning("Discarding invalid scene payload: %s", reason)
                data.pop("scene", None)
                data.pop("scene_yaml", None)
                data["validation_error"] = reason
                data["validation_target"] = "scene"
                data["response"] = (
                    "I couldn't create a valid scene from that request: "
                    f"{reason}. Please refine the request and try again."
                )
                if data.get("intent") == "scene":
                    data["intent"] = "answer"
            else:
                data["scene"] = normalized
                data["scene_yaml"] = yaml.dump(
                    normalized, default_flow_style=False, allow_unicode=True
                )

        if data.get("automation"):
            is_valid, reason, normalized = validate_automation_payload(data["automation"], hass)

            # The presence + duration phrase ("when nobody is in X for N
            # minutes") unlocks two things downstream:
            #   * aggressive entity substitution (the LoRA reliably
            #     hallucinates ``lock.front_door`` for these prompts no
            #     matter the real subject — we'd rather fall back to
            #     ANY controllable entity that shares a token with the
            #     prompt than dead-end on a clarification);
            #   * the trigger-recovery branch firing even when the
            #     validator's original reason was about the entity, so
            #     the now-patched automation still gets its synthesized
            #     state trigger with the prompt's ``for: {N}`` window.
            has_presence_duration = _has_presence_for_duration(user_message or "")

            if not is_valid and reason and "unknown entity_id" in reason and entities:
                patched, subs = _resolve_unknown_entity_ids(
                    reason,
                    data["automation"],
                    entities,
                    hass,
                    user_message=user_message,
                    aggressive=has_presence_duration,
                )
                if patched is not None and subs:
                    is_valid_retry, retry_reason, normalized_retry = validate_automation_payload(
                        patched, hass
                    )
                    if is_valid_retry and normalized_retry is not None:
                        _LOGGER.info(
                            "Auto-corrected %d entity_id substitution(s) in automation: %s",
                            len(subs),
                            subs,
                        )
                        data["automation"] = patched
                        is_valid, reason, normalized = True, "", normalized_retry
                    elif retry_reason and "trigger" in retry_reason.lower():
                        # Entity substitution succeeded; the only
                        # remaining gap is a missing/invalid trigger.
                        # Persist the patch and update ``reason`` so the
                        # trigger-recovery branch below operates on the
                        # now-valid action targets — otherwise the patch
                        # would be silently dropped and the trigger
                        # synth would walk the still-broken actions and
                        # fall through to "Discarding invalid".
                        _LOGGER.info(
                            "Applied %d entity_id substitution(s); "
                            "trigger still missing/invalid (%s): %s",
                            len(subs),
                            retry_reason,
                            subs,
                        )
                        data["automation"] = patched
                        reason = retry_reason

            # Coerce wrong-shape triggers (state instead of numeric_state,
            # time instead of sun) when the prompt hints at the right shape;
            # re-validate and on success replace the original automation.
            if normalized is not None and user_message:
                coerced = _apply_prompt_aware_coercions(
                    data["automation"], user_message, hass, entities
                )
                if coerced is not None:
                    data["automation"] = coerced
                    normalized = coerced

            # Last-ditch trigger recovery — when validation rejected for a
            # missing/invalid trigger, synthesize it from the prompt. Also
            # fires for the presence + duration phrasing whenever the
            # automation is still invalid: the LoRA's malformed time-of-day
            # trigger ("for 10 minutes" → ``at: 10:00``) doesn't carry the
            # word "trigger" in the validator reason, but the prompt's
            # ``for N minutes`` is a strong-enough signal to rewrite it.
            should_recover_trigger = (
                bool(user_message)
                and not is_valid
                and reason
                and (("trigger" in reason.lower()) or has_presence_duration)
            )
            if should_recover_trigger:
                coerced = _apply_prompt_aware_coercions(
                    data["automation"], user_message, hass, entities
                )
                if coerced is not None:
                    _LOGGER.info(
                        "Recovered missing/invalid trigger from prompt for automation: %s", reason
                    )
                    data["automation"] = coerced
                    is_valid, reason, normalized = True, "", coerced

            if not is_valid or normalized is None:
                _LOGGER.warning("Discarding invalid architect automation payload: %s", reason)
                data.pop("automation", None)
                data.pop("automation_yaml", None)
                data["validation_error"] = reason
                data["validation_target"] = "automation"
                data["response"] = _humanise_unknown_entity_error(reason, entities)
                if data.get("intent") == "automation":
                    data["intent"] = "clarification" if "unknown entity_id" in reason else "answer"
            else:
                data["automation"] = normalized
                data["automation_yaml"] = yaml.dump(
                    normalized, default_flow_style=False, allow_unicode=True
                )
                data["risk_assessment"] = assess_automation_risk(normalized)

        _strip_markers_for_proposal_intents(data)
        return data

    except (
        json.JSONDecodeError,
        KeyError,
        ValueError,
    ):
        _LOGGER.error("Failed to parse architect response: %s", text[:500])
        return {"intent": "answer", "response": text}


def parse_suggestions(text: str, provider_name: str) -> list[dict[str, Any]]:
    """Parse the LLM response into automation configs."""
    try:
        _LOGGER.debug("Raw %s response: %s", provider_name, text[:500])

        # Strip markdown fences if present
        text = text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text[3:]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()

        # Find JSON array in response
        start = text.find("[")
        end = text.rfind("]")
        if start == -1 or end == -1:
            _LOGGER.warning("No JSON array found in %s response", provider_name)
            return []
        text = text[start : end + 1]

        suggestions = json.loads(text)
        if not isinstance(suggestions, list):
            return []

        valid = []
        for s in suggestions:
            if not isinstance(s, dict):
                continue
            has_name = "alias" in s or "description" in s
            has_behavior = any(k in s for k in ("actions", "action", "triggers", "trigger"))
            if has_name and has_behavior:
                valid.append(s)
        if len(valid) < len(suggestions):
            _LOGGER.debug(
                "Filtered %d/%d suggestions (missing required keys)",
                len(suggestions) - len(valid),
                len(suggestions),
            )
        return valid

    except (json.JSONDecodeError, KeyError, IndexError) as exc:
        _LOGGER.warning("Failed to parse %s response: %s", provider_name, exc)
        return []


def parse_command_response_text(text: str) -> ArchitectResponse:
    """Parse LLM response text into service calls."""
    try:
        text = text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text[3:]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()

        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1:
            return {"calls": [], "response": "Could not parse LLM response"}

        result = json.loads(text[start : end + 1])
        if not isinstance(result, dict):
            return {"calls": [], "response": "Invalid response format"}

        return {
            "calls": result.get("calls", []),
            "response": result.get("response", "Command processed"),
        }

    except (json.JSONDecodeError, KeyError) as exc:
        _LOGGER.warning("Failed to parse command response: %s", exc)
        return {"calls": [], "response": "Failed to parse LLM response"}


def parse_streamed_response(
    text: str,
    hass: HomeAssistant,
    entities: list[EntitySnapshot] | None = None,
    tool_log: list[dict[str, Any]] | None = None,
    *,
    session_id: str | None = None,
    user_message: str | None = None,
    language: str | None = None,
) -> ArchitectResponse:
    """Parse completed streamed text.

    Looks for a ```automation ... ``` fenced block.  Text before it is the
    conversational response; the block contents are parsed as automation JSON.
    Falls back to parse_architect_response for pure-JSON responses.

    When *entities* is provided, command-intent results are validated
    through ``apply_command_policy`` so that unsafe calls are blocked
    even on the streaming path.

    When *tool_log* is provided and includes an ``execute_command``
    invocation, command JSON that duplicates an already-executed tool
    call is suppressed to prevent double execution.
    """
    # Extract quick_actions block first — it's supplementary and can
    # appear alongside any other block type. Removing it now also lets
    # the duplicate-strip below see a terminal ```command``` block when
    # the model emits the order: prose → command → quick_actions.
    quick_actions: list[dict[str, Any]] | None = None
    qa_match = re.search(r"```quick_actions\s*\n?([\s\S]*?)```", text)
    if qa_match:
        try:
            parsed_qa = json.loads(qa_match.group(1).strip())
            if isinstance(parsed_qa, list) and parsed_qa:
                quick_actions = parsed_qa
        except (
            json.JSONDecodeError,
            ValueError,
        ):
            _LOGGER.warning("Failed to parse quick_actions block")
        text = text[: qa_match.start()] + text[qa_match.end() :]

    def _attach_qa(r: ArchitectResponse) -> ArchitectResponse:
        if quick_actions and r.get("intent") != "command_approval":
            r["quick_actions"] = quick_actions
        _strip_markers_for_proposal_intents(r)
        return r

    # If execute_command ran during the tool loop, the user has already
    # seen that action take effect. A trailing ```command``` block that
    # echoes a *duplicate* (service, entity_id, data) signature would
    # re-execute, so we filter those individual calls out of the block.
    # Calls that don't match an executed signature stay (the model may
    # legitimately mix a tool-fired call with another immediate call in
    # one turn — and for ``toggle`` services in particular, re-running
    # would undo the user's request).
    # NEVER strip ```delayed_command``` — a scheduled future action is
    # by definition not a duplicate of an already-fired immediate one,
    # and dropping it would silently lose the user's request (e.g.
    # "turn the fan on now and off in 10 minutes").
    executed_sigs = _executed_call_signatures(tool_log) if tool_log else set()
    # When the entire command block is removed because every call inside
    # was a duplicate of an executed tool call, the model's prose
    # confirmation ("Turning off the kitchen light.") still ran via the
    # tool. We track this so the fallback path can mark the result as
    # suppressed and the policy preserves the prose instead of stomping
    # it with the "I didn't run any action" clarification.
    command_block_fully_stripped = False
    if executed_sigs:
        # Allow optional trailing whitespace, not just end-of-string,
        # because the quick_actions extraction above may leave behind
        # blank lines between the command block and the (now-removed)
        # quick_actions slot.
        cmd_match = re.search(r"```command\s*\n?([\s\S]*?)```\s*\Z", text.rstrip())
        if cmd_match:
            stripped_text = text.rstrip()
            # Prose before the block is what the policy's auto-repair
            # uses to infer the intended verb ("Opening the garage door"
            # → cover.open_cover). Pass it to _call_signature so a
            # malformed echo collapses onto the repaired signature.
            prose_before_block = stripped_text[: cmd_match.start()]
            try:
                block_data = json.loads(cmd_match.group(1).strip())
                block_calls = block_data.get("calls", [])
                if isinstance(block_calls, list) and block_calls:
                    surviving: list[dict[str, Any]] = []
                    for c in block_calls:
                        sig = (
                            _call_signature(c, prose_before_block) if isinstance(c, dict) else None
                        )
                        if sig is not None and sig in executed_sigs:
                            continue
                        surviving.append(c)
                    if len(surviving) < len(block_calls):
                        if surviving:
                            rewritten_data = dict(block_data, calls=surviving)
                            new_block = (
                                "```command\n"
                                + json.dumps(rewritten_data, ensure_ascii=False)
                                + "\n```"
                            )
                            text = stripped_text[: cmd_match.start()] + new_block
                        else:
                            text = stripped_text[: cmd_match.start()]
                            command_block_fully_stripped = True
            except (
                json.JSONDecodeError,
                ValueError,
            ):
                # Malformed block — leave it; downstream parser logs the warning.
                pass

    # Check for immediate command fenced block — anchored to end.
    command_match = re.search(r"```command\s*\n?([\s\S]*?)```\s*$", text)
    if command_match:
        response_text = text[: command_match.start()].strip()
        json_text = command_match.group(1).strip()
        try:
            data = json.loads(json_text)
            result: ArchitectResponse = {
                "intent": "command",
                "response": response_text or "Done.",
                "calls": data.get("calls", []),
            }
            if entities is not None:
                result = apply_command_policy(
                    result, entities, hass=hass, session_id=session_id, language=language
                )
            return _attach_qa(result)
        except (
            json.JSONDecodeError,
            ValueError,
        ):
            _LOGGER.warning("Failed to parse command block: %s", json_text[:200])

    cancel_match = re.search(r"```cancel\s*\n?([\s\S]*?)```\s*$", text)
    if cancel_match:
        response_text = text[: cancel_match.start()].strip()
        try:
            data = json.loads(cancel_match.group(1).strip())
            return _attach_qa(
                {
                    "intent": "cancel",
                    "response": data.get("response", response_text or "Cancelled."),
                }
            )
        except (
            json.JSONDecodeError,
            ValueError,
        ):
            # Malformed cancel block — fall through to avoid destructive action
            _LOGGER.warning("Failed to parse cancel block, ignoring")

    # Check for delayed_command fenced block — anchored to end so
    # informational examples don't trigger real scheduling.
    delay_match = re.search(r"```delayed_command\s*\n?([\s\S]*?)```\s*$", text)
    if delay_match:
        response_text = text[: delay_match.start()].strip()
        json_text = delay_match.group(1).strip()
        try:
            data = json.loads(json_text)
            result: ArchitectResponse = {
                "intent": "delayed_command",
                "response": response_text or "Scheduling that action.",
                "calls": data.get("calls", []),
            }
            if "delay_seconds" in data:
                result["delay_seconds"] = data["delay_seconds"]
            if "scheduled_time" in data:
                result["scheduled_time"] = data["scheduled_time"]
            if entities is not None:
                result = apply_command_policy(
                    result, entities, hass=hass, session_id=session_id, language=language
                )
            return _attach_qa(result)
        except (
            json.JSONDecodeError,
            ValueError,
        ):
            _LOGGER.warning("Failed to parse delayed_command block: %s", json_text[:200])

    # Check for scene fenced block. The prompt asks the model to put
    # the block at the end of the response, but some cloud models
    # violate that and emit trailing prose ("This scene will ensure
    # …"). Anchoring to end would drop the proposal entirely and the
    # chat would show only prose with no Accept & Save card. Match
    # the LAST ``` scene ``` block anywhere in the text instead — scene
    # proposals are always gated behind the Accept card, so a stray
    # example block can't auto-create.
    scene_json: str | None = None
    scene_start = scene_end = -1
    scene_fenced: re.Match[str] | None = None
    for m in re.finditer(r"```scene\s*\n?([\s\S]*?)```", text):
        scene_fenced = m
    if scene_fenced is not None:
        scene_json = scene_fenced.group(1).strip()
        scene_start, scene_end = scene_fenced.start(), scene_fenced.end()
    else:
        # Bare scene block fallback — model dropped the opening ``` and
        # emitted `scene\n{...}`. Balanced decode so neither nested JSON
        # objects nor trailing prose braces mis-bound the body.
        bare = _find_bare_block(text, "scene")
        if bare is not None:
            scene_json, scene_start, scene_end = bare
    if scene_json is not None:
        from ..scene_utils import validate_scene_payload

        # Splice prose around the block so any trailing summary stays
        # in the bubble. Strip a single blank-line separator on either
        # side so the joined prose doesn't collapse into a double gap.
        prose_before = text[:scene_start].rstrip()
        prose_after = text[scene_end:].lstrip()
        response_text = "\n\n".join(p for p in (prose_before, prose_after) if p).strip()
        json_text = scene_json
        try:
            scene_data = json.loads(json_text)
            is_valid, reason, normalized = validate_scene_payload(scene_data, hass)
            if not is_valid or normalized is None:
                return _attach_qa(
                    {
                        "intent": "answer",
                        "response": response_text or "I couldn't create a valid scene",
                        "validation_error": reason,
                        "validation_target": "scene",
                    }
                )
            scene_result: dict[str, Any] = {
                "intent": "scene",
                "response": response_text or "Scene created.",
                "scene": normalized,
                "scene_yaml": yaml.dump(normalized, default_flow_style=False, allow_unicode=True),
            }
            # Preserve refine_scene_id so the streaming handler can
            # update the existing scene instead of creating a new one.
            if scene_data.get("refine_scene_id"):
                scene_result["refine_scene_id"] = scene_data["refine_scene_id"]
            return _attach_qa(scene_result)
        except (
            json.JSONDecodeError,
            ValueError,
        ):
            _LOGGER.warning("Failed to parse scene block: %s", json_text[:200])

    # Check for automation fenced block. The prompt asks the model to put
    # the proposal at the END, but some models append a trailing summary
    # ("This automation monitors all 5 leak detectors…"). A strict
    # end-anchor would drop those proposals (no Accept card, raw JSON
    # leaking in the bubble); matching the last block ANYWHERE would do
    # the opposite — misclassify an illustrative ```automation example in
    # a how-to answer as a real proposal.
    #
    # The reliable discriminator is the USER's intent, not the trailing
    # text (a short "adjust the entity IDs" example and a short real
    # summary look identical by length/keywords). So: a TERMINAL block is
    # always the proposal; a block with trailing prose counts only when
    # the user actually asked to CREATE an automation. A how-to /
    # "show me an example" question (``_is_definite_automation`` is False)
    # leaves its non-terminal example rendered as code.
    wants_automation = _is_definite_automation(user_message or "")

    def _is_proposal(end_pos: int) -> bool:
        return text[end_pos:].strip() == "" or wants_automation

    auto_json: str | None = None
    auto_start = auto_end = -1
    auto_fenced: re.Match[str] | None = None
    for m in re.finditer(r"```automation\s*\n?([\s\S]*?)```", text):
        if _is_proposal(m.end()):
            auto_fenced = m
    if auto_fenced is not None:
        auto_json = auto_fenced.group(1).strip()
        auto_start, auto_end = auto_fenced.start(), auto_fenced.end()
    else:
        # Fallback when the model drops the OPENING ``` and emits a bare
        # `automation\n{...}` block (with or without a stray closing
        # fence). Balanced decode (not a greedy `\{[\s\S]*\}`) so trailing
        # prose containing a `}` — a follow-up sentence with `{}`, a Jinja
        # `{{ … }}` snippet — can't extend the capture past the JSON
        # object and break json.loads. The body MUST start with `{`
        # (architect emits JSON, not YAML) and the `automation` token MUST
        # be alone on its own line, so prose mentions like "I built an
        # automation for you" don't trigger. Same terminal/intent gate as
        # the fenced path.
        bare = _find_bare_block(text, "automation")
        if bare is not None and _is_proposal(bare[2]):
            auto_json, auto_start, auto_end = bare
    if auto_json is not None:
        # Splice prose around the block so any trailing summary stays in
        # the bubble (mirrors the scene path).
        prose_before = text[:auto_start].rstrip()
        prose_after = text[auto_end:].lstrip()
        if prose_after:
            # The model appended a trailing summary after the proposal;
            # we repositioned it back into the bubble.
            record_repair("trailing_marker_reposition")
        response_text = "\n\n".join(p for p in (prose_before, prose_after) if p).strip()
        json_text = auto_json
        try:
            automation_data = json.loads(json_text)
            is_valid, reason, normalized = validate_automation_payload(automation_data, hass)
            # See ``parse_architect_response`` for the rationale on
            # ``has_presence_duration`` — it unlocks aggressive entity
            # substitution AND the prompt-driven trigger recovery for
            # the duration_misread / presence_duration buckets.
            has_presence_duration = _has_presence_for_duration(user_message or "")
            # Auto-correct an unknown-entity rejection when the model
            # named a real device with the wrong domain prefix
            # (light.coffee_maker → switch.coffee_maker) OR echoed an
            # example entity (lock.front_door) when the prompt names
            # exactly one real device. Mirrors the JSON-only path.
            if not is_valid and reason and "unknown entity_id" in reason and entities:
                patched, subs = _resolve_unknown_entity_ids(
                    reason,
                    automation_data,
                    entities,
                    hass,
                    user_message=user_message,
                    aggressive=has_presence_duration,
                )
                if patched is not None and subs:
                    is_valid_retry, retry_reason, normalized_retry = validate_automation_payload(
                        patched, hass
                    )
                    if is_valid_retry and normalized_retry is not None:
                        _LOGGER.info(
                            "Auto-corrected %d entity_id substitution(s) "
                            "in streamed automation: %s",
                            len(subs),
                            subs,
                        )
                        automation_data = patched
                        is_valid, reason, normalized = (
                            True,
                            "",
                            normalized_retry,
                        )
                    elif retry_reason and "trigger" in retry_reason.lower():
                        # Persist the entity patch so the trigger
                        # recovery branch below operates on the now-
                        # valid action targets. See the matching block
                        # in parse_architect_response for the full
                        # rationale.
                        _LOGGER.info(
                            "Applied %d entity_id substitution(s) in "
                            "streamed automation; trigger still missing "
                            "(%s): %s",
                            len(subs),
                            retry_reason,
                            subs,
                        )
                        automation_data = patched
                        reason = retry_reason
            # Apply prompt-aware trigger coercions (state →
            # numeric_state, time → sun) when the model emitted a
            # valid-but-wrong trigger shape that downstream checks
            # reject. Mirrors the JSON-only path.
            if normalized is not None and user_message:
                coerced = _apply_prompt_aware_coercions(
                    automation_data, user_message, hass, entities
                )
                if coerced is not None:
                    automation_data = coerced
                    normalized = coerced
            # Last-ditch trigger recovery — when validation rejected the
            # automation for a missing or invalid trigger, synthesize it
            # from the prompt's presence + duration / sun / numeric
            # phrasing. Mirrors the JSON-only path at parse_architect_response.
            # Also fires for presence+duration phrasing even when the
            # validator's reason was about the entity, since the LoRA
            # routinely emits a time-of-day trigger for "for N minutes"
            # and the time-trigger reason doesn't contain the word
            # "trigger" in some validator variants.
            should_recover_trigger = (
                bool(user_message)
                and not is_valid
                and reason
                and (("trigger" in reason.lower()) or has_presence_duration)
            )
            if should_recover_trigger:
                coerced = _apply_prompt_aware_coercions(
                    automation_data, user_message, hass, entities
                )
                if coerced is not None:
                    _LOGGER.info(
                        "Recovered missing/invalid trigger from prompt for streamed automation: %s",
                        reason,
                    )
                    automation_data = coerced
                    is_valid, reason, normalized = True, "", coerced
            if not is_valid or normalized is None:
                _LOGGER.warning("Discarding invalid streamed automation payload: %s", reason)
                # Always surface the device-listing clarification when
                # validation rejects — matches the JSON-mode path so a
                # user gets the same actionable list regardless of
                # transport. Preserve any pre-block prose the LoRA
                # emitted by prefixing it to the humanised clarification.
                humanised = _humanise_unknown_entity_error(reason, entities)
                bubble = f"{response_text}: {humanised}" if response_text else humanised
                return _attach_qa(
                    {
                        "intent": ("clarification" if "unknown entity_id" in reason else "answer"),
                        "response": bubble,
                        "validation_error": reason,
                        "validation_target": "automation",
                    }
                )
            automation_yaml = yaml.dump(normalized, default_flow_style=False, allow_unicode=True)
            return _attach_qa(
                {
                    "intent": "automation",
                    "response": response_text or "Here's the automation I've created.",
                    "automation": normalized,
                    "automation_yaml": automation_yaml,
                    "description": normalized.get("description", ""),
                    "risk_assessment": assess_automation_risk(normalized),
                }
            )
        except (
            json.JSONDecodeError,
            ValueError,
        ):
            _LOGGER.warning("Failed to parse automation block: %s", json_text[:200])

    # No fenced block — try the old JSON-only parser. Pass entities
    # so an "unknown entity_id" rejection becomes a clarification
    # listing the user's actual devices instead of the bare reason.
    # Threading user_message lets parse_architect_response apply
    # the prompt-aware entity-substitution fallback (e.g. the LoRA
    # echoes ``lock.front_door`` from the prompt's EXAMPLES section
    # when the actual target is ``switch.coffee_maker`` — without
    # user_message the resolver can't find the prompt-named entity
    # and dead-ends as clarification). Same goes for the
    # prompt-aware trigger coercions (state → numeric_state, time
    # → sun).
    result = parse_architect_response(text, hass, entities, user_message=user_message)

    # The prose we're returning is trustworthy and should bypass
    # the policy's unbacked-action guard in three cases:
    # (a) The model wrote the prose alongside a ```command``` block
    #     that fully matched an executed tool signature (the
    #     command_block_fully_stripped branch). The prose is
    #     anchored to actions that really ran AND it doesn't claim
    #     a different known entity. The latter check is the safety
    #     correction — previously this branch trusted any prose,
    #     letting a mismatched "Turning off the bedroom" claim
    #     through after stripping a kitchen-targeted block.
    # (b)/(c) A tool call already fired AND the prose matches one
    #     of the trusted shapes from _prose_is_trusted_after_tool
    #     (synthesized prefix, generic ack, or executed-entity
    #     describe).
    if result.get("intent") == "answer" and tool_log:
        executed_calls = _executed_service_calls_from_log(tool_log)
        response_text = result.get("response", "")
        # Also accept the case where the LLM attempted execute_command
        # / validate_action with a service whose action verb appears in
        # the prose. The formal trust check requires ``executed:True``
        # entries; if a service errored or returned an unexpected shape
        # but the LLM clearly identified the entity (the tool referenced
        # it), the strict "no entity matched" stomp below would be
        # wrong. The narrower attempted-call test prevents the stomp
        # from misfiring without trusting prose for unrelated tools.
        attempted_match = _prose_describes_attempted_call(response_text, tool_log, entities)
        if (
            attempted_match
            or _prose_is_trusted_after_tool(response_text, executed_calls, entities)
            or (
                command_block_fully_stripped
                and executed_calls
                and not _response_names_unbacked_entity(response_text, executed_calls, entities)
            )
        ):
            result["suppressed_duplicate_command"] = True

    # Promote REVIEW-bucket tool attempts to a proper command_approval
    # proposal even when the LLM narrated the requires_approval result
    # back at the user instead of emitting a command JSON block. Without
    # this the chat shows "I can't unlock the door because it requires
    # approval" with no card — the user has no way to actually approve.
    # Also runs when the LLM directly emitted ``intent: "command_approval"``
    # (no tool_log needed) so we can mint a proposal_id and attach the
    # four sentinel quick-actions the chat UI needs.
    result = synthesize_approval_from_tool_log(result, tool_log, hass, language=language)

    # Apply command safety policy if entities are available.
    # Always run the policy — even when calls is empty — so that
    # command intents with no calls get downgraded to "answer".
    if entities is not None:
        result = apply_command_policy(
            result, entities, hass=hass, session_id=session_id, language=language
        )

    return _attach_qa(result)
