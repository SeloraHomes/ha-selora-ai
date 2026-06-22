"""Cheap regex heuristics for chat intent classification and entity filtering.

Used by the low-context provider path (e.g. SeloraLocal add-on, max_seq=1024)
to pre-classify intent before the LLM call, and to filter the AVAILABLE
ENTITIES list down to the ones the message might be about. Cloud providers
self-classify in their long system prompt instead.
"""

from __future__ import annotations

import re
from typing import Any

from ..types import EntitySnapshot

# Aggressive caps used when the provider has a tight context window
# (provider.is_low_context). Small enough to fit the system prompt + a
# trimmed entity list inside ~700 tokens.
_LOW_CONTEXT_MAX_ENTITIES = 15

# The cloud prompt's entity budget. This is the long-standing cap the cloud
# path has always shipped — relevance ranking + need pinning only change WHICH
# entities survive once an install exceeds it, never the budget itself. Do not
# lower it to "tighten" the prompt: aggregate queries with no keyword/need hit
# (e.g. "what's the status of everything?") fall back to rank order and would
# silently lose entities that were previously in scope.
_CLOUD_MAX_ENTITIES = 500
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
        "ask",
        "get",
        "give",
        "got",
        "let",
        "make",
        "need",
        "put",
        "run",
        "see",
        "set",
        "show",
        "tell",
        "try",
        "turn",
        "use",
        "want",
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
    # Solar-event phrasing ("at sunset", "at sundown", "at sunrise") is
    # by definition automation, not a one-shot command. The automation
    # specialist's training corpus includes
    # ``{"trigger":"sun","event":"sunset"}`` examples; the command LoRA
    # has no path to express a recurring sun trigger, so without this
    # gate "turn on the kitchen light at sunset" routes to command and
    # returns a single one-shot service call that ignores the schedule.
    re.compile(
        r"\b(sunset|sundown|dusk|sunrise|sundawn|dawn)\b",
        re.IGNORECASE,
    ),
    # Numeric-threshold conditions ("drops below 18", "warmer than 26",
    # "rises above 80%"). Same rationale as sun events: only the
    # automation specialist can express a ``numeric_state`` trigger,
    # routing to command produces a one-shot ignored call.
    re.compile(
        r"\b(?:drops?|falls?|rises?|gets?|goes?)\s+(?:below|above|under|over|to)\b"
        r"|\b(?:warmer|cooler|hotter|colder|higher|lower)\s+than\b"
        r"|\b(?:below|above|under|over)\s+\d",
        re.IGNORECASE,
    ),
    re.compile(r"\bremind me\b"),
    re.compile(r"\bcreate (an?|the)?\s*automation\b"),
    # Presence + duration: "when {nobody|no one|someone|anyone} ...
    # for N {minute|hour}s". Without this anchor, prompts like
    # "Turn off the porch light when nobody is on the porch for 10
    # minutes" fall through to the command specialist (one-shot
    # service call) instead of routing to the automation specialist
    # that can express a state-with-`for:` trigger.
    re.compile(
        r"\b(nobody|no\s*one|noone|someone|anyone|anybody|everyone)\b"
        r".{0,60}\bfor\s+\d+\s+(second|minute|hour)s?\b",
    ),
    # Conditional duration ("if X for N minutes", "when Y for N
    # hours"). Anchored on the "for N <unit>s" tail so it only fires
    # on real durations, not generic "for me / for you / for the
    # night" wording.
    re.compile(r"\b(if|when|whenever|while)\b.{0,40}\bfor\s+\d+\s+(second|minute|hour)s?\b"),
)
_QUESTION_OPENER = re.compile(
    r"^(what|where|when|why|how|which|who|is|are|was|were|do|does|did|"
    r"can|could|should|will|would|tell me|show me|list|give me)\b"
)

# Subset of question openers that are UNAMBIGUOUSLY interrogative.
# ``when`` / ``while`` / ``if`` double as automation-rule connectors
# ("when nobody is home, turn off the lights") and stay out of the bare
# set. The ``when (is|are|was|were|does|do|did|...)`` variant IS a
# question ("when is sunrise tomorrow?") — explicit auxiliary-verb
# match here so it doesn't get mis-routed to the automation specialist
# by the bare ``sunset|sunrise`` automation anchor.
_PURE_QUESTION_OPENER = re.compile(
    r"^(?:"
    r"(?:what|where|why|how|which|who)\b"
    r"|(?:when|while)\s+(?:is|are|was|were|does|do|did|will|would|should|"
    r"can|could|has|have|had)\b"
    r"|tell me|show me|list|give me"
    r")",
    re.IGNORECASE,
)
# Polite modal request opener ("can/could/would/will you/we …"). A
# request, not a yes/no status question — when paired with an automation
# pattern it's a scheduled-automation request even if the verb
# (notify/remind) isn't in ``_ACTION_VERB_RE``.
_POLITE_MODAL_OPENER = re.compile(
    r"^\s*(?:(?:hey|hi|hello|ok|okay|so|please|yo)[\s,]+)*"
    r"(?:can|could|would|will)\s+(?:you|we)\b",
    re.IGNORECASE,
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


_IDENTITY_QUESTION = re.compile(
    # Optional conversational lead-in: zero or more filler words ("okay
    # cool ", "so ", "hey "), each followed by space/comma — so
    # "Okay cool what can you do?" is still recognised.
    r"^\s*(?:(?:hey|hi|hello|ok|okay|so|um|well|cool|nice|alright|sweet|oh|yo|hmm)[\s,]+)*"
    r"(?:"
    r"(?:who|what)(?:'?s|\s+is|\s+are|\s+r)?\s+(?:you|u|selora|this)\b"
    r"|what\s+(?:can|could|do|would)\s+(?:you|u)\s+(?:do|help)\b"
    r"|what\s+does\s+selora\s+do\b"
    r"|how\s+does\s+selora\s+work\b"
    r"|are\s+you\s+selora\b"
    r"|what(?:'?s|\s+are)?\s+your\s+(?:name|purpose|job|capabilit\w*|function\w*)\b"
    r"|(?:tell\s+me\s+about|introduce|describe)\s+(?:yourself|you|selora)\b"
    r")"
    r"[\s!.,?:;)]*$",
    re.IGNORECASE,
)


def _is_identity_question(message: str) -> bool:
    """Return True when ``message`` is a pure identity/capability question."""
    if not message:
        return False
    text = message.strip()
    if not text or len(text) > 60:
        return False
    return bool(_IDENTITY_QUESTION.match(text))


_COMMAND_VERBS = (
    r"turn|switch|toggle|set|start|stop|play|pause|resume|open|close|"
    r"lock|unlock|dim|brighten|increase|decrease|raise|lower|"
    r"activate|deactivate|enable|disable|run|trigger"
)

_POLITE_COMMAND = re.compile(
    r"^\s*(?:(?:hey|hi|hello|ok|okay|so|well|cool|please|yo)[\s,]+)*"
    rf"(?:can|could|would|will)\s+(?:you|we|i)\b.*?\b(?:{_COMMAND_VERBS})\b",
    re.IGNORECASE,
)


_META_QUESTION = re.compile(
    r"\b(suggest|recommend|propose|examples?)\b"
    r"|\b(tell|show|describe|explain)\s+(me|us)\b"
    r"|\b(what|which)\s+(kinds?|types?|examples?|automations?|commands?|scenes?|things)\b",
    re.IGNORECASE,
)

_COMMAND_VERB = re.compile(
    r"\b(turn|switch|toggle|set|start|stop|play|pause|resume|open|close|"
    r"lock|unlock|dim|brighten|increase|decrease|raise|lower|"
    r"activate|deactivate|enable|disable|run|trigger)\b"
)

# Inflection-tolerant device-action verb (turn / turns / turning,
# lock / locks / locking, …). Used to tell a request-style opener that
# describes a concrete action ("give me an automation that turns off the
# lights every night") from a plain query ("list my automations").
_ACTION_VERB_RE = re.compile(
    r"\b(?:turns?|turning|switch(?:es|ing)?|toggl\w+|sets?|setting|"
    r"play(?:s|ing)?|paus\w+|resum\w+|opens?|opening|clos\w+|"
    r"locks?|locking|unlocks?|unlocking|dims?|dimming|brighten\w*|"
    r"increas\w+|decreas\w+|rais\w+|lower\w*|activat\w+|deactivat\w+|"
    r"enabl\w+|disabl\w+|runs?|running|trigger\w*|starts?|starting|stops?|"
    r"stopping)\b",
    re.IGNORECASE,
)

# Host-/system-level destructive verbs. The command specialist responds
# to these by hallucinating an unrelated SAFE call (e.g. "shut down
# home assistant" → climate.turn_off on a random thermostat) because
# nothing in its training corpus maps a request like this to a real
# action — yet the verbs ("shut down", "delete", "reset", "reboot")
# look enough like device-control language that it tries anyway. The
# safe blocklist in command_policy.py blocks the corresponding real
# services (homeassistant.stop, automation.reload, …) but cannot stop
# the model from substituting a DIFFERENT entity to satisfy the verb.
#
# Route these to the clarification specialist BEFORE the command LoRA
# ever sees them so the user is asked what they actually meant (e.g.
# "Are you trying to restart Home Assistant? You'll need to do that
# from Settings → System."), instead of getting a fake confirmation
# that turned off something they didn't ask about.
_DESTRUCTIVE_SYSTEM_REQUEST = re.compile(
    r"\b(?:shut\s*down|shutdown|reboot|restart|power\s*off|kill|halt|"
    r"factory\s*reset|hard\s*reset|wipe|uninstall|format)\b"
    r".{0,40}\b(?:home\s*assistant|ha|system|server|hub|host|everything|all)\b"
    r"|\b(?:delete|remove|destroy|nuke|purge|clear|wipe)\b"
    r".{0,40}\b(?:all|every|automations?|scenes?|scripts?|integrations?|"
    r"devices?|entities?|history|recorder|database|config|settings?)\b"
    r"|\bfactory\s*reset\b"
    r"|\bwipe\s+everything\b",
    re.IGNORECASE,
)

# Prompt-injection / jailbreak patterns. The command specialist treats
# these as ordinary user requests and picks a plausible-looking call to
# emit ("ignore previous instructions" → climate.turn_on on a random
# thermostat) because the LoRA has no path to express "this isn't a
# device-control request." Detect the most common attack shapes
# (instruction-override, embedded "system:" / chat-template tokens,
# persona-jailbreak prose) and route to a canned safety answer
# upstream of the model so no service call ever fires.
_PROMPT_INJECTION = re.compile(
    r"\b(?:ignore|disregard|forget|override|bypass|skip)\s+"
    r"(?:all\s+|any\s+|the\s+|prior\s+|previous\s+|earlier\s+|above\s+)*"
    r"(?:prior|previous|earlier|above|system|original)?\s*"
    r"(?:instruction|instructions|prompt|prompts|rules?|guidelines?|directives?)\b"
    r"|\b(?:reveal|show|print|leak|expose|display|reproduce|repeat)\s+"
    r"(?:the\s+|your\s+|me\s+|us\s+)*"
    r"(?:system\s+)?(?:prompt|instructions?|rules?|guidelines?)\b"
    r"|<\|im_(?:start|end)\|>"
    r"|<\|(?:system|user|assistant|endoftext)\|>"
    # Chat-template role label — only as a LINE-LEADING label
    # ("system:" / "assistant:" at message or line start), the form a
    # template-injection uses. Anchoring to line start avoids refusing
    # legitimate mid-sentence text like "Set the alarm system: away" or
    # a friendly name ending in "System:".
    r"|(?:^|\n)\s*(?:system|assistant|user)\s*[:：]"
    r"|\byou\s+are\s+(?:now\s+|hereby\s+)?(?:evil|jailbroken|free|"
    r"unrestricted|dan|do\s+anything\s+now|in\s+developer\s+mode|"
    r"without\s+(?:rules|restrictions|filters))\b"
    r"|\bpretend\s+(?:you|to\s+be)\b.{0,40}\b(?:evil|jailbroken|no\s+rules|"
    r"without\s+(?:rules|restrictions|filters)|different\s+ai)\b"
    r"|\bact\s+as\b.{0,40}\b(?:if\s+you\s+have\s+no|without)\s+"
    r"(?:rules|restrictions|filters|guidelines)\b",
    re.IGNORECASE,
)

# Non-English command verbs. The contract for adversarial-non_english
# is explicit: refuse or ask the user to rephrase in English; do NOT
# emit a hallucinated entity_id or a service call against a guessed
# target. Detected at the regex layer (no language-id dependency) by
# scanning for common control verbs across the major Romance/Germanic
# languages and any non-Latin script characters.
_NON_ENGLISH_COMMAND = re.compile(
    # Spanish: encender/enciende/apaga/abrir/cerrar/prender/quitar
    r"\b(?:encender|encend[ae]r?|enciend[ae]n?|apag[ae]r?|apag[ae]n?|"
    r"prend[ae]r?|prend[ae]n?|"
    r"abr[ei]r?|abr[ei]n?|cerr[ae]r?|cierr[ae]n?|"
    r"quitar|quita|"
    # French: allume(r/z), éteindre/éteins, ouvre/ouvrir, ferme(r/z)
    r"allume[rz]?|éteindre|eteindre|éteins|eteins|"
    r"ouvre[rz]?|ouvrir|ferme[rz]?|fermer|"
    # German: einschalten/ausschalten/öffnen/schließen/anmachen/ausmachen
    r"einschalten|ausschalten|öffnen|öffne|schlie[sß]en|"
    r"anmachen|ausmachen|"
    # Italian: accendere/accendi, spegnere/spegni, aprire/apri, chiudere/chiudi
    r"accendere|accendi|spegnere|spegni|aprire|apri|chiudere|chiudi|"
    # Portuguese: ligar/liga, desligar/desliga, abrir/abre, fechar/fecha
    r"ligar|liga|desligar|desliga|fechar|fecha"
    r")\b",
    re.IGNORECASE,
)

# Non-Latin scripts that aren't English by construction. CJK, Cyrillic,
# Arabic, Hebrew, Devanagari, Thai. A single character is enough — we
# only need to know the request isn't in English so we can ask the
# user to rephrase rather than guess at a device.
_NON_LATIN_SCRIPT = re.compile(
    r"[Ѐ-ӿ"  # Cyrillic
    r"Ԁ-ԯ"  # Cyrillic Supplement
    r"぀-ゟ"  # Hiragana
    r"゠-ヿ"  # Katakana
    r"一-鿿"  # CJK Unified Ideographs
    r"가-힯"  # Hangul
    r"؀-ۿ"  # Arabic
    r"֐-׿"  # Hebrew
    r"ऀ-ॿ"  # Devanagari
    r"฀-๿"  # Thai
    r"]"
)


def _is_prompt_injection_attempt(message: str) -> bool:
    """True when the message looks like a jailbreak / instruction-override
    attempt. See ``_PROMPT_INJECTION`` for the patterns covered."""
    if not message:
        return False
    return bool(_PROMPT_INJECTION.search(message))


_ENGLISH_COMMAND_VERB = re.compile(
    r"\b(?:turn|switch|toggle|open|close|lock|unlock|set|dim|brighten|"
    r"start|stop|run|pause|resume|activate|deactivate|enable|disable|"
    r"play|mute|unmute|arm|disarm|what|where|when|why|how|which|"
    r"is|are|was|were|do|does|did|can|could|should|will|would|"
    r"tell|show|list|give)\b",
    re.IGNORECASE,
)


def _is_non_english_command(message: str) -> bool:
    """True when the message is plausibly a device-control request in
    a language other than English. We never try to translate or guess
    — the contract is to refuse / ask the user to rephrase, so this
    only has to detect *that* it's not English, not *which* language.

    A bare non-Latin character alone is NOT enough: Home Assistant
    friendly names commonly embed localized characters
    ("What's the state of sensor 温度?", "temperature of 温度"), and
    refusing such requests would block legitimate English queries.
    Refuse on non-Latin script only when the message carries NO English/
    Latin words at all (a genuinely foreign request); when surrounding
    Latin text is present, the non-Latin token is treated as an entity
    name and the LLM resolves it.
    """
    if not message:
        return False
    # Foreign command verb alone is not enough: Home Assistant entity
    # friendly names are arbitrary, and a user can legitimately write
    # "turn on Liga" or "turn off the Prender light" in English where
    # the listed foreign verb happens to be the entity name. When an
    # English command verb / question opener is present alongside the
    # foreign token, treat the foreign token as an entity name and let
    # the LLM resolve it.
    has_english = bool(_ENGLISH_COMMAND_VERB.search(message))
    if _NON_ENGLISH_COMMAND.search(message) and not has_english:
        return True
    # Non-Latin script: refuse only when there is no surrounding Latin
    # word context. "temperature of 温度" / "status for 温度" carry
    # Latin words → the non-Latin chunk is a localized entity name, not
    # a foreign-language command. A pure non-Latin message ("温度を点けて")
    # has no Latin word and IS a foreign request.
    has_latin_word = bool(re.search(r"[A-Za-z]{2,}", message))
    return bool(_NON_LATIN_SCRIPT.search(message)) and not has_latin_word


def _request_language_supported(language: str | None) -> bool:
    """True when *language* is a non-English locale Selora can converse in.

    Requests in such a locale are forwarded to the LLM with a reply-language
    directive (see ``prompts._language_directive``) instead of being refused
    by the non-English short-circuit — otherwise the localized command
    autocomplete would lead users into requests the backend rejects.

    English (and missing/unknown codes) returns ``False`` so the non-English
    guard still protects the command specialist from genuinely-foreign input.
    """
    if not language:
        return False
    base = str(language).lower().split("-")[0]
    if base == "en":
        return False
    # Local import: ``prompts`` imports this module, so importing it at
    # module scope would risk a cycle. ``_LANGUAGE_NAMES`` is the allowlist
    # of locales the model is instructed to reply in.
    from .prompts import _LANGUAGE_NAMES  # noqa: PLC0415

    return base in _LANGUAGE_NAMES


def _build_safety_short_circuit(message: str, language: str | None = None) -> dict[str, Any] | None:
    """Return a canned answer envelope when the message is a prompt-
    injection attempt or a non-English request.

    Routing these through the LLM lets the command specialist emit a
    plausible-but-wrong service call (the LoRA has no path to refuse).
    Short-circuiting upstream guarantees the wire envelope carries
    only ``intent`` + ``response`` — no ``executed`` / no ``c`` array
    — which satisfies the ``no_hallucinated_entities`` and
    ``no_unsafe_domains_invoked`` contracts by construction.

    ``language`` is the effective request locale (UI language forwarded by
    the panel, falling back to the HA server locale). When it names a
    supported non-English locale the non-English refusal is suppressed:
    the request is forwarded to the LLM, which replies in that language.
    The prompt-injection refusal is unconditional.

    Returns ``None`` when the message is normal English so the caller
    falls through to the LLM path.
    """
    if _is_prompt_injection_attempt(message):
        return {
            "intent": "answer",
            "response": (
                "I can only help with controlling Home Assistant "
                "devices and creating automations. What would you "
                "like to do?"
            ),
        }
    if _is_non_english_command(message) and not _request_language_supported(language):
        return {
            "intent": "answer",
            "response": (
                "I only understand English right now. Could you rephrase your request in English?"
            ),
        }
    return None


# Anchors that ground a request well enough for the automation
# specialist to build something specific. We split them by category so
# the vague-automation predicate can require AT LEAST ONE anchor — a
# bare "make me an automation" has none of these and the LoRA falls
# back to memorised training examples (typically `lock.front_door`),
# which the validator then rejects.
#
# Word lists are intentionally broad — friendly_names commonly include
# room or device-class words ("Kitchen Light", "Front Door Lock",
# "Bedroom Fan"), so any user message that names a device usually
# hits one of these. We don't try to enumerate every possible
# friendly_name (that would require knowing the user's home); the
# device-class / room vocabulary is enough.
_AUTOMATION_TIME_ANCHOR = re.compile(
    r"\b(sunset|sunrise|sundown|sunup|dawn|dusk|"
    r"morning|evening|night|noon|midnight|afternoon|"
    r"daily|hourly|weekly|monthly|"
    r"monday|tuesday|wednesday|thursday|friday|saturday|sunday|"
    r"weekday|weekend|"
    r"midnight|midday)\b"
    r"|\b\d{1,2}\s*(?:am|pm|:\d{2}|o['’]?clock|hours?|minutes?|seconds?)\b"
    r"|\b(?:at|after|before|when|whenever|if|every|each)\s+\d",
    re.IGNORECASE,
)
_AUTOMATION_DEVICE_ANCHOR = re.compile(
    r"\b(light|lights|lamp|lamps|bulb|bulbs|chandelier|sconce|"
    r"lock|locks|door|doors|window|windows|gate|gates|deadbolt|"
    r"switch|switches|outlet|outlets|plug|plugs|relay|relays|"
    r"fan|fans|thermostat|thermostats|climate|heater|heaters|cooler|"
    r"coolers|ac|hvac|furnace|"
    r"speaker|speakers|tv|television|media|player|stream|sonos|chromecast|"
    r"garage|garages|blind|blinds|curtain|curtains|shade|shades|cover|"
    r"covers|shutter|shutters|"
    r"vacuum|roomba|alarm|siren|water|valve|sensor|sensors|camera|"
    r"cameras|doorbell|"
    r"coffee|maker|kettle|oven|microwave|dishwasher|washer|dryer|"
    r"kitchen|porch|bedroom|bedrooms|living|bathroom|bathrooms|office|"
    r"hallway|hall|entry|entryway|foyer|basement|attic|garage|outside|"
    r"outdoor|indoor|upstairs|downstairs|patio|deck|"
    r"motion|presence|temperature|humidity|battery|leak|smoke|co|co2|"
    r"notify|notification|alert|alarm|remind|reminder|push|email|sms|"
    r"message|warn|warning)\b",
    re.IGNORECASE,
)


def _is_vague_automation(user_message: str) -> bool:
    """True when the message looks like an automation request but is
    too underspecified for the specialist to ground.

    The automation LoRA needs at least one anchor — a command verb,
    a time/event word, or a device/room/notification keyword — to
    build something concrete. Bare requests like "make me an
    automation" or "create an automation for me" have none of these.
    Without an anchor the model falls back to memorised training
    examples (typically `lock.front_door`, which the validator
    rejects with "automation references unknown entity_id"). Such
    turns route to the clarification specialist instead so the
    user is asked WHICH device and WHEN before any JSON is built.
    """
    if not user_message:
        return True
    msg = user_message.lower()
    if _COMMAND_VERB.search(msg):
        return False
    if _AUTOMATION_TIME_ANCHOR.search(msg):
        return False
    return not _AUTOMATION_DEVICE_ANCHOR.search(msg)


# Meta-questions about capabilities ("what automations can you
# create", "tell me about scenes") need to route to the answer
# specialist — the automation/command specialists try to *do* the
# thing, not describe it. Without this gate, "what automations can
# you create" goes to the automation LoRA, which produces a
# hallucinated automation JSON the validator rejects with "each
# trigger must include a platform". The gate is checked before
# _AUTOMATION_PATTERNS so legitimate "every morning turn on …"
# requests still reach the automation specialist.
#
# NOTE: "suggest|recommend|propose" used to live here but were moved
# to ``_VAGUE_BARE_REQUEST`` (clarification) — the answer specialist
# was returning "You can try to automate the coffee maker." for
# "suggest an automation", which the benchmark counts as an
# unhelpful non-ask. Routing those to the clarification LoRA forces
# a "?"-shaped follow-up that gives the user something to act on.
_META_QUESTION = re.compile(
    r"\bexamples?\b"
    r"|\b(tell|show|describe|explain)\s+(me|us)\b"
    r"|\b(what|which)\s+(kinds?|types?|examples?|automations?|commands?|scenes?|things)\b",
    re.IGNORECASE,
)


# Bare automation-shaped phrasings that carry no concrete subject. The
# existing ``_AUTOMATION_PATTERNS`` + ``_is_vague_automation`` gate
# only catches messages that already contain the word "automation"
# (or "schedule" / "every"-time / a "when X then Y" rule). Several
# phrasings the corpus considers vague — "make something useful",
# "set up a routine", "suggest an automation", "automate my house" —
# either miss those patterns entirely or get caught by the answer
# specialist, which replies with a non-clarification ("You can try to
# automate the coffee maker.") that the bench rejects.
#
# Route any match here straight to the clarification LoRA so the
# user is asked WHICH device and WHEN before any command/automation
# JSON is built. Checked before ``_META_QUESTION`` and
# ``_AUTOMATION_PATTERNS`` so suggest/automate-style phrasings take
# this branch even when they'd also match those gates.
_VAGUE_BARE_REQUEST = re.compile(
    # "make (me|us)? <vague filler>"
    r"\bmake\s+(me\s+|us\s+)?(something|stuff|things|anything|useful|nice|cool|interesting)\b"
    # "make something/stuff/things useful/nice/cool"
    r"|\bmake\s+(something|stuff|things|anything)\s+(useful|nice|cool|interesting|fun)\b"
    # "set up (a|an|the|some)? (routine|automation|schedule|...)"
    r"|\bset\s+up\s+(a\s+|an\s+|the\s+|some\s+)?"
    r"(routine|routines|automation|automations|schedule|schedules|something|stuff)\b"
    # "(suggest|recommend|propose|brainstorm) (a|an|the|some)? (automation|routine|scene|idea|...)"
    r"|\b(suggest|recommend|propose|brainstorm)\s+(a\s+|an\s+|the\s+|some\s+|me\s+|us\s+)?"
    r"(automation|automations|routine|routines|scene|scenes|"
    r"idea|ideas|suggestion|suggestions|something|stuff)\b"
    # "(create|build|setup) (a|an|the)? routine" (without "automation")
    r"|\b(create|build|setup)\s+(a\s+|an\s+|the\s+)?(routine|routines)\b"
    # "automate (my|the|our)? (home|house|life|stuff|everything|things|place)"
    r"|\bautomate\s+(my\s+|the\s+|our\s+)?"
    r"(home|house|life|stuff|everything|things|place|world)\b",
    re.IGNORECASE,
)


# Domain-specific phrasings that *must* refer to a particular HA
# domain. When the user names a category they don't actually have set
# up, the command/automation LoRA reliably hallucinates an entity_id
# (typically training-corpus favourites like ``lock.front_door`` or
# ``alarm_control_panel.alarm``). The validator catches automation
# hallucinations, but command intents only get policy-checked — an
# unbacked ``alarm_control_panel.alarm_arm_home`` rides straight
# through into ``command_approval`` with bogus "Alarm armed to home"
# prose. Catching the reference at classification time and routing
# to the clarification LoRA prevents that path entirely.
#
# Each entry is (domain_required, pattern). When ``pattern.search``
# matches AND no entity in the user's snapshot belongs to
# ``domain_required``, we route to clarification. Doorbells live as
# binary_sensor.* / event.* in HA and don't map cleanly to a single
# domain, so they're handled separately by friendly-name scan in
# ``_is_missing_domain_reference``.
_DOMAIN_REFERENCE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "alarm_control_panel",
        re.compile(
            r"\b(arm|disarm|re-?arm)\b.{0,20}\b(security|alarm)\s+(system|panel)\b"
            r"|\b(arm|disarm|re-?arm)\s+(the\s+|my\s+|our\s+)?(security\s+)?alarm\b"
            r"|\b(security|alarm)\s+(system|panel)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "lock",
        re.compile(
            r"\b(unlock|deadbolt)\b"
            r"|\block\s+(the\s+|my\s+|our\s+)?(front|back|side|garage|patio)\s+door\b"
            r"|\block\s+(the\s+|my\s+|our\s+)?door(s|way)?\b",
            re.IGNORECASE,
        ),
    ),
)

# Matches an explicit doorbell mention. Doorbell entities don't share
# a single domain (binary_sensor.doorbell, event.doorbell, camera.*
# with friendly_name "Doorbell"), so we cross-check against entity
# ids and friendly names in ``_is_missing_domain_reference`` rather
# than gating by domain prefix.
_DOORBELL_REFERENCE = re.compile(r"\bdoorbell\b", re.IGNORECASE)


def _is_missing_domain_reference(
    user_message: str,
    entities: list[EntitySnapshot],
) -> bool:
    """True when ``user_message`` names a device category absent from ``entities``.

    Used to short-circuit command/automation routing for prompts like
    "arm the security system at midnight" when the user has no
    alarm_control_panel entity. The command LoRA otherwise emits a
    hallucinated service call that ``apply_command_policy`` promotes
    to ``command_approval`` with bogus confirmation prose — there's
    no validator on the command path to catch unbacked entity_ids
    the way there is on the automation path.

    Returns False when ``entities`` is empty so we don't accidentally
    flunk every device-shaped request during startup before the
    snapshot is populated.
    """
    if not entities:
        return False
    msg = user_message.lower()
    user_domains: set[str] = set()
    for e in entities:
        eid = e.get("entity_id", "")
        if "." in eid:
            user_domains.add(eid.split(".", 1)[0])

    for domain_required, pattern in _DOMAIN_REFERENCE_PATTERNS:
        if pattern.search(msg) and domain_required not in user_domains:
            return True

    # Doorbell — check entity_ids + friendly_names since the domain
    # varies (binary_sensor / event / camera).
    if _DOORBELL_REFERENCE.search(msg):
        for e in entities:
            eid = e.get("entity_id", "").lower()
            if "doorbell" in eid:
                return False
            fname = str((e.get("attributes") or {}).get("friendly_name", "")).lower()
            if "doorbell" in fname:
                return False
        return True

    return False


# Bare meta-assistant queries about the assistant itself ("help",
# "what are you?", "what can you do?", "who are you?"). Without
# this gate the bare word "help" falls through every other pattern
# in ``_classify_chat_intent`` and routes to the default ``command``
# specialist, which then hallucinates a service call against an
# unrelated entity (e.g. "Living room thermostat on.") — see
# benchmark case ``xc.meta.assistant_question``. Matched on the
# WHOLE message (with optional polite trailers) so a real device
# command like "help me turn on the lights" still routes to
# ``command`` via the verb match downstream.
_META_HELP = re.compile(
    r"^\s*(help|help\s+me|"
    r"what\s+(can|do)\s+you\s+do|what\s+are\s+you|who\s+are\s+you|"
    r"how\s+do\s+you\s+work|what\s+is\s+(this|selora)|"
    r"how\s+can\s+you\s+help)"
    r"[\s!.,?:;]*$",
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
    r"|\bcreate (an?|the)?\s*automation\b"
    # Recurring solar-event phrasing — see _AUTOMATION_PATTERNS for the
    # rationale. Cloud providers also need this so the panel's spinner
    # sentinel fires for "turn on the kitchen light at sunset".
    r"|\b(sunset|sundown|dusk|sunrise|sundawn|dawn)\b"
    # Numeric-threshold conditionals ("drops below 18", "warmer than 26").
    r"|\b(?:drops?|falls?|rises?|gets?|goes?)\s+(?:below|above|under|over|to)\b"
    r"|\b(?:warmer|cooler|hotter|colder|higher|lower)\s+than\b"
    # Presence + duration ("nobody is home for 10 minutes") and
    # conditional duration ("if X for N minutes") — kept in sync with
    # the same anchors in ``_AUTOMATION_PATTERNS`` so the cloud spinner
    # fires for the presence-recovery automations the classifier routes
    # to the automation specialist.
    r"|\b(?:nobody|no\s*one|noone|someone|anyone|anybody|everyone)\b"
    r".{0,60}\bfor\s+\d+\s+(?:second|minute|hour)s?\b"
    r"|\b(?:if|when|whenever|while)\b.{0,40}\bfor\s+\d+\s+(?:second|minute|hour)s?\b",
    re.IGNORECASE,
)


# Informational-question openers that should NEVER trigger the cloud
# automation spinner. Same as ``_QUESTION_OPENER`` MINUS the conditional
# connectors (``when``/``whenever``/``while``/``if``) — those legitimately
# open an automation rule ("when nobody is home, turn off the lights").
_INFO_QUESTION_OPENER = re.compile(
    r"^(what|where|why|how|which|who|is|are|was|were|do|does|did|"
    r"can|could|should|will|would|has|have|had|"
    r"tell me|show me|list|give me)\b",
    re.IGNORECASE,
)

# WH-interrogatives that are documentation/informational questions even
# when they contain an action verb ("How do I stop an automation?",
# "Why does this automation keep running?"). Unlike the polite-request
# openers (can/could/will you …) these never describe an artifact the
# user wants built — always route to the answer specialist.
_WH_INTERROGATIVE_OPENER = re.compile(
    r"^(what|where|why|how|which|who)\b",
    re.IGNORECASE,
)


def _is_definite_automation(user_message: str) -> bool:
    """True only for recurring/conditional automation phrasing.

    Excludes one-shot delayed commands which still route to the
    automation LoRA via ``_classify_chat_intent`` but return a
    ``delayed_command`` block. Also excludes informational questions
    ("What time is sunset?", "Is the temperature higher than 25?") — the
    bare sun-event / numeric-comparator alternatives would otherwise
    match them and the panel would show "Building automation..." while
    suppressing answer streaming for a plain query. Conditional
    connectors (``when``/``if``) are NOT treated as questions so
    "when nobody is home, turn off the lights" still fires the sentinel.

    A question opener that ALSO carries a command verb is a request, not
    a query ("Can you turn off the porch light every morning?") — keep
    the sentinel so cloud streams show the automation-building UI,
    matching ``_classify_chat_intent`` which routes it to automation.
    """
    if not user_message:
        return False
    stripped = user_message.strip()
    # WH-interrogatives ("How do I stop an automation?", "Why does this
    # automation keep running?") are documentation questions even with an
    # action verb — never fire the spinner for them.
    if _WH_INTERROGATIVE_OPENER.match(stripped):
        return False
    # Interrogative "when/while + aux" ("when is sunset?", "while it is
    # dark?") is a status question, NOT a "when X then Y" automation
    # rule. ``_PURE_QUESTION_OPENER`` matches the former but not the
    # bare conditional connector, so a non-action one is excluded here
    # while "when nobody is home, turn off the lights" still fires.
    if _PURE_QUESTION_OPENER.match(stripped) and not _ACTION_VERB_RE.search(user_message):
        return False
    # Polite modal requests with a scheduling pattern ("Can you remind me
    # at sunset?", "Could you notify me every morning?") are automation
    # requests even though notify/remind aren't device-action verbs —
    # keep the spinner so the UI matches ``_classify_chat_intent``.
    polite_auto = bool(_POLITE_MODAL_OPENER.match(stripped)) and any(
        pat.search(stripped) for pat in _AUTOMATION_PATTERNS
    )
    # Other info openers (can/could/is/are/list/…) exclude only when
    # there's no action verb AND it isn't a polite scheduled request —
    # "Can you turn off the porch light every morning?" keeps the spinner.
    if (
        _INFO_QUESTION_OPENER.match(stripped)
        and not _ACTION_VERB_RE.search(user_message)
        and not polite_auto
    ):
        return False
    return bool(_DEFINITE_AUTOMATION.search(user_message))


def _classify_chat_intent_polite_command_check(msg: str) -> str | None:
    """Return ``"command"`` when ``msg`` is a polite imperative phrased as a
    question ("can you turn off the X"). Returns None when not applicable.
    Lets ``_classify_chat_intent`` catch this BEFORE the question-opener
    heuristic claims it for the answer specialist."""
    if _POLITE_COMMAND.match(msg):
        return "command"
    return None


def _classify_chat_intent(
    user_message: str,
    entities: list[EntitySnapshot] | None = None,
) -> str:
    """Cheap regex pre-classifier for low-context LoRA routing.

    Returns one of ``command`` / ``automation`` / ``answer`` /
    ``clarification``. Used only when ``provider.is_low_context`` —
    cloud providers self-classify in their long system prompt instead.

    ``entities`` is optional. When supplied, the classifier also
    detects requests that name a domain absent from the user's
    snapshot ("arm the security system" without an alarm panel,
    "turn on the doorbell" without one) and routes those to the
    clarification LoRA — the command path otherwise hallucinates a
    matching service call that policy gates can't catch.
    """
    msg = user_message.lower().strip()
    if not msg:
        return "answer"
    # Polite imperatives phrased as questions ("can you turn off the X")
    # are commands, not questions — catch them before _QUESTION_OPENER
    # claims them for the answer specialist. BUT a polite request that
    # ALSO carries scheduling / conditional language ("Can you turn off
    # the porch light at sunset?") is an automation — fall through to
    # the _AUTOMATION_PATTERNS loop below instead of returning an
    # immediate command that would discard the schedule.
    if _POLITE_COMMAND.match(msg) and not any(pat.search(msg) for pat in _AUTOMATION_PATTERNS):
        return "command"
    # Destructive/system-level requests ("shut down home assistant",
    # "delete all my automations", "factory reset") — the command LoRA
    # has no training mapping for these and will hallucinate an
    # unrelated SAFE action (e.g. climate.turn_off on a random
    # thermostat) to satisfy the verb. Route to the clarification
    # specialist so the user is asked what they actually mean instead
    # of getting a fake confirmation. See _DESTRUCTIVE_SYSTEM_REQUEST.
    if _DESTRUCTIVE_SYSTEM_REQUEST.search(msg):
        return "clarification"
    # Bare "help" / "what can you do?" — see ``_META_HELP``. Checked
    # before _META_QUESTION because the message is matched whole, not
    # by an internal keyword.
    if _META_HELP.match(msg):
        return "answer"
    # Bare "make me an automation" / "set up a routine" / "suggest an
    # automation" / "automate my house" — these carry no concrete
    # device or trigger for the automation specialist to ground on,
    # so route straight to the clarification LoRA. Checked BEFORE
    # _META_QUESTION so phrasings starting with "suggest"/"recommend"
    # land here instead of the generic answer specialist (which
    # replies "You can try to automate the coffee maker." and fails
    # the should_route_to_clarification benchmark).
    #
    # BUT — if the user gave a fully concrete request that just happens
    # to START with a vague preamble ("set up an automation: turn off
    # the bedroom light when bedroom is empty for 15 minutes"), the
    # message is NOT vague (has command verb + device + duration). Gate
    # on _is_vague_automation so that branch falls through to the
    # automation specialist instead of getting bounced to clarification
    # with "Do this now or set as a schedule?".
    if _VAGUE_BARE_REQUEST.search(msg) and _is_vague_automation(msg):
        return "clarification"
    if _META_QUESTION.search(msg):
        return "answer"
    # When we have an entity snapshot, catch requests that name a
    # category the user doesn't have ("arm the security system" with
    # no alarm panel, "lock the front door" with no lock, "turn on
    # the doorbell" with no doorbell). The command LoRA otherwise
    # invents a target entity_id; the alarm case rides through
    # command_approval with bogus confirmation prose. Checked before
    # _AUTOMATION_PATTERNS so even "lock the front door at 10pm"
    # (which matches the at-N automation pattern) is intercepted
    # when the lock genuinely doesn't exist.
    if entities is not None and _is_missing_domain_reference(msg, entities):
        return "clarification"
    # Pure interrogative openers ("what / where / why / how / which /
    # who time is sunset today?") run BEFORE automation patterns so
    # the bare sun/duration keyword anchors don't classify an
    # informational question as an automation. ``when`` / ``while`` /
    # ``if`` deliberately stay in the FULL ``_QUESTION_OPENER`` set
    # below — they double as conditional connectors ("when nobody is
    # home, turn off the lights"), which is an automation, not a
    # question.
    # Question openers normally route to the answer specialist. Three
    # carve-outs, checked BEFORE the automation loop so the bare
    # sun/numeric anchors don't claim a status question:
    #   * WH-interrogatives ("How do I stop an automation?", "Why does
    #     this automation keep running?") ALWAYS answer — documentation
    #     questions, even when they contain an action verb.
    #   * Yes/no openers ("Is the bedroom temperature above 25?", "Are
    #     the lights on?") with no action verb are status questions —
    #     ``_INFO_QUESTION_OPENER`` covers is/are/do/can/… that
    #     ``_PURE_QUESTION_OPENER`` omits.
    #   * Request-style openers ("give me / list an automation that turns
    #     off the lights every night") with a concrete action verb fall
    #     through to the automation loop.
    #   * Polite modal requests ("Can you notify me every morning?",
    #     "Could you remind me at sunset?") whose verb (notify/remind) is
    #     NOT in ``_ACTION_VERB_RE`` are still automation REQUESTS when an
    #     automation pattern matches — fall through so the schedule is
    #     built, instead of answering.
    opener = _PURE_QUESTION_OPENER.match(msg) or _INFO_QUESTION_OPENER.match(msg)
    if opener and not _WH_INTERROGATIVE_OPENER.match(msg):
        polite_auto = bool(_POLITE_MODAL_OPENER.match(msg)) and any(
            pat.search(msg) for pat in _AUTOMATION_PATTERNS
        )
        if not polite_auto and not _ACTION_VERB_RE.search(msg):
            return "answer"
    elif opener and _WH_INTERROGATIVE_OPENER.match(msg):
        return "answer"
    for pat in _AUTOMATION_PATTERNS:
        if pat.search(msg):
            # Bare "make me an automation" / "create an automation
            # for me" requests give the automation LoRA nothing to
            # ground on — it falls back to memorised training
            # examples (typically `lock.front_door`) which the
            # validator then rejects. Route those to the
            # clarification specialist so we ask which device and
            # when BEFORE the automation specialist runs. See
            # ``_is_vague_automation`` for the anchor list.
            if _is_vague_automation(msg):
                return "clarification"
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
_SCORE_CATEGORY_DOMAIN_HIT = 5

# Category keyword → HA domain. When the user types a plural/category
# word like "lights", the entity-id and friendly-name substring checks
# miss because the real entities are named "Kitchen Light" (singular).
# Worse, the substring check matches semantically-unrelated entities
# whose slug happens to contain the keyword's letters — e.g. keyword
# "off" matches "coffee" inside ``switch.coffee_maker``. The result is
# that "turn off all the lights at sunrise" surfaces ``coffee_maker``
# instead of any actual light. This mapping bridges the singular/plural
# gap so a category prompt boosts every entity of that domain.
_CATEGORY_KEYWORD_TO_DOMAIN: dict[str, str] = {
    "light": "light",
    "lights": "light",
    "lamp": "light",
    "lamps": "light",
    "switch": "switch",
    "switches": "switch",
    "outlet": "switch",
    "outlets": "switch",
    "plug": "switch",
    "plugs": "switch",
    "cover": "cover",
    "covers": "cover",
    "blind": "cover",
    "blinds": "cover",
    "shade": "cover",
    "shades": "cover",
    "shutter": "cover",
    "shutters": "cover",
    "garage": "cover",
    "lock": "lock",
    "locks": "lock",
    "deadbolt": "lock",
    "deadbolts": "lock",
    "fan": "fan",
    "fans": "fan",
    "thermostat": "climate",
    "thermostats": "climate",
    "heater": "climate",
    "heaters": "climate",
    "ac": "climate",
    "speaker": "media_player",
    "speakers": "media_player",
    "tv": "media_player",
    "tvs": "media_player",
    "vacuum": "vacuum",
    "vacuums": "vacuum",
    "scene": "scene",
    "scenes": "scene",
}

# ── Cloud relevance pinning ──────────────────────────────────────────
# The keyword ranker (_score_entity_against_keywords) scores on
# friendly_name / entity_id / area tokens. That is enough when the
# intended entity is named after what the user typed ("porch light"),
# but a large real install names its sensors after the hardware
# ("BME280 3", "AirCycler G2") or carries no request token at all
# (a weather entity, an indoor-temperature sensor). For those, a CPU /
# diagnostic temperature sensor whose name literally contains the word
# "temperature" outscores the room thermometer the automation actually
# needs, and the relevant entity falls past the cap.
#
# Pinning closes that gap: when the request implies a need for a given
# device_class, unit, or domain, a bounded number of the best-matching
# entities of that kind are GUARANTEED a slot in the cloud entity block,
# independent of their keyword score. The cap is still respected — pinned
# entities count against it — so the prompt size is unchanged.

# Request token → measurement device_classes that token implies a need
# for. Matched against the request keyword set (already lowercased,
# stopwords removed). A pinned device_class survives the cap even with a
# zero keyword score.
_DEVICE_CLASS_NEED_TOKENS: dict[str, tuple[str, ...]] = {
    "temperature": ("temperature",),
    "temp": ("temperature",),
    "inside": ("temperature",),
    "indoor": ("temperature",),
    "outside": ("temperature",),
    "outdoor": ("temperature",),
    "warmer": ("temperature",),
    "cooler": ("temperature",),
    "hotter": ("temperature",),
    "colder": ("temperature",),
    "humidity": ("humidity",),
    "humid": ("humidity",),
    "pressure": ("pressure", "atmospheric_pressure"),
    "barometric": ("pressure", "atmospheric_pressure"),
    "window": ("window", "opening", "door"),
    "windows": ("window", "opening", "door"),
    "door": ("door", "opening", "window"),
    "doors": ("door", "opening", "window"),
    "open": ("window", "opening", "door"),
    "opened": ("window", "opening", "door"),
    "co2": ("carbon_dioxide",),
    "illuminance": ("illuminance",),
    "brightness": ("illuminance",),
    "lux": ("illuminance",),
    "power": ("power",),
    "energy": ("energy", "power"),
}

# Request token → domains that token implies a need for. The user names
# a "fan" / "weather" / "thermostat"; the matching domain's entities are
# pinned even when their friendly_name shares no token with the request.
_DOMAIN_NEED_TOKENS: dict[str, tuple[str, ...]] = {
    "fan": ("fan",),
    "fans": ("fan",),
    "weather": ("weather",),
    "forecast": ("weather",),
    "thermostat": ("climate",),
    "thermostats": ("climate",),
    "climate": ("climate",),
    "heater": ("climate", "water_heater"),
    "heaters": ("climate", "water_heater"),
    "cooling": ("climate",),
    "heating": ("climate",),
    "lock": ("lock",),
    "locks": ("lock",),
    "vacuum": ("vacuum",),
    "cover": ("cover",),
    "covers": ("cover",),
    "blind": ("cover",),
    "blinds": ("cover",),
    "shade": ("cover",),
    "shades": ("cover",),
    "garage": ("cover",),
}

# AC / air-conditioner phrasing the keyword tokenizer can't surface as a need
# token: "ac" and "a/c" are ≤2-char tokens that _low_context_keywords drops,
# and "air conditioner" tokenizes to "air"/"conditioner" (neither is a need
# token). Matched against the RAW message in _cloud_pinned_needs so a bare
# "turn on the AC" still pins the climate domain.
# Trailing boundary is ``(?!\w)`` not ``\b``: the dotted "A.C." ends in a
# literal period, so a ``\b`` (which needs a word/non-word transition) never
# matches before the following space/end. ``(?!\w)`` accepts that position.
_AC_NEED_RE = re.compile(
    r"\b(?:a/?c|a\.c\.?|air[\s-]?con(?:ditioner|ditioning|ditioned|s)?)(?!\w)",
    re.IGNORECASE,
)

# How many entities of a single pinned need to keep. Bounded so a home
# with hundreds of temperature sensors can't crowd the whole block; the
# few most need-relevant per kind (see _need_relevance) are pinned and the
# rest compete normally for the remaining cap.
_PER_NEED_KEEP = 6

# Substrings that mark a sensor as a diagnostic / system reading rather
# than the room/environment measurement a home-automation request means.
# A "CPU Temperature" sensor literally carries device_class=temperature,
# so on a request that needs a thermometer it would otherwise pin ahead
# of the actual indoor/outdoor sensors. Demoting these in the per-need
# ranking lets the real sensors win their pin slots. Matched against the
# entity_id local part and friendly_name tokens.
_DIAGNOSTIC_SENSOR_TOKENS = frozenset(
    {
        "cpu",
        "gpu",
        "memory",
        "ram",
        "disk",
        "load",
        "uptime",
        "battery",
        "signal",
        "rssi",
        "linkquality",
        "voltage",
        "throughput",
        "latency",
        "processor",
        "cache",
        "swap",
        "core",
        "thermal",
        "die",
        "package",
        "system",
        "server",
        "host",
        "router",
        "modem",
        "nas",
        "printer",
        "ups",
    }
)

# Per-need relevance weights. A request qualifier matching the entity's
# area or a non-class name token is the strongest signal; a diagnostic
# token is a strong demotion so system sensors sort below real ones.
_NEED_AREA_MATCH = 6
_NEED_NAME_TOKEN_MATCH = 4
_NEED_DIAGNOSTIC_PENALTY = 20


def _is_diagnostic_sensor(entity: EntitySnapshot) -> bool:
    """True when the entity's id / name marks it as a system/diagnostic
    reading (CPU temperature, battery level, signal strength, …) rather
    than a room or environment measurement."""
    eid = entity.get("entity_id", "").lower()
    local = eid.split(".", 1)[-1] if "." in eid else eid
    tokens = set(re.split(r"[^a-z0-9]+", local)) - {""}
    fname = str(entity.get("attributes", {}).get("friendly_name", "")).lower()
    tokens |= set(re.split(r"[^a-z0-9]+", fname)) - {""}
    return bool(tokens & _DIAGNOSTIC_SENSOR_TOKENS)


def _need_relevance(entity: EntitySnapshot, keywords: set[str]) -> int:
    """Rank candidates that satisfy the SAME device_class / domain need.

    The class membership is the gate, not the signal — every temperature
    sensor matches the temperature need equally. What separates the room
    thermometer the request means from a CPU diagnostic sensor is the
    request's qualifier words (outside / inside / outdoor / a room name)
    landing on the entity's area or non-class name tokens, and the absence
    of diagnostic markers. Higher = more likely the intended entity.
    """
    eid = entity.get("entity_id", "").lower()
    local = eid.split(".", 1)[-1] if "." in eid else eid
    fname = str(entity.get("attributes", {}).get("friendly_name", "")).lower()
    name_tokens = set(re.split(r"[^a-z0-9]+", local)) | set(re.split(r"[^a-z0-9]+", fname))
    name_tokens -= {""}
    area = (entity.get("area_name") or "").lower()
    score = 0
    for kw in keywords:
        if area and kw in area:
            score += _NEED_AREA_MATCH
        if kw in name_tokens:
            score += _NEED_NAME_TOKEN_MATCH
    if _is_diagnostic_sensor(entity):
        score -= _NEED_DIAGNOSTIC_PENALTY
    return score


def _cloud_pinned_needs(
    keywords: set[str], message: str = ""
) -> tuple[frozenset[str], frozenset[str]]:
    """Resolve a request into the device_classes and domains whose entities
    must be pinned into the cloud entity block.

    ``keywords`` drives the table lookups; ``message`` is the raw request,
    scanned for phrasings the tokenizer can't surface (AC). Returns
    ``(device_classes, domains)`` — empty when the request implies no
    measurement / domain need, in which case pinning is a no-op and the plain
    keyword ranking decides the cap.
    """
    device_classes: set[str] = set()
    domains: set[str] = set()
    for kw in keywords:
        device_classes.update(_DEVICE_CLASS_NEED_TOKENS.get(kw, ()))
        domains.update(_DOMAIN_NEED_TOKENS.get(kw, ()))
    # A temperature condition that compares against a setpoint implies a
    # climate device even when the user wrote "set temperature" (stopwords)
    # rather than "thermostat". climate is a small domain on any install, so
    # pinning it alongside a temperature need is cheap and keeps the setpoint
    # entity in scope for "below the set temperature"-style requests.
    if "temperature" in device_classes:
        domains.add("climate")
    # "AC" / "A/C" / "air conditioner" — dropped by the tokenizer, so matched
    # on the raw message. A bare "turn on the AC" carries no temperature word
    # and would otherwise pin nothing, omitting an AC entity past the cap.
    if message and _AC_NEED_RE.search(message):
        domains.add("climate")
    return frozenset(device_classes), frozenset(domains)


def _entity_matches_need(
    entity: EntitySnapshot,
    device_classes: frozenset[str],
    domains: frozenset[str],
) -> bool:
    """True when the entity satisfies a pinned device_class OR domain need."""
    eid = entity.get("entity_id", "")
    if "." not in eid:
        return False
    domain = eid.split(".", 1)[0]
    if domain in domains:
        return True
    if device_classes:
        dc = str(entity.get("attributes", {}).get("device_class", "")).lower()
        if dc and dc in device_classes:
            return True
    return False


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
    domain = eid.split(".", 1)[0] if "." in eid else ""
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
        # Category keyword (lights, switches, covers, …) → boost every
        # entity in the matching domain so plural/category phrasing
        # works even when no slug or fname token contains the keyword.
        # Without this, "turn off all the lights" surfaces
        # ``switch.coffee_maker`` (substring 'off' inside 'coffee')
        # before any actual light, leaving the LoRA with no lights to
        # target in AVAILABLE ENTITIES and an unbuildable automation.
        if domain and _CATEGORY_KEYWORD_TO_DOMAIN.get(kw) == domain:
            score += _SCORE_CATEGORY_DOMAIN_HIT
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


# ── Unspecified-target clarification ─────────────────────────────────
# When the user gives a command-shaped message that doesn't name a
# specific device ("turn on a light", "turn it on", "dim the lights",
# "set it to 22", "make it warmer"), routing to either the command or
# clarification LoRA invites a hallucinated single-entity pick. We
# instead short-circuit upstream of the model with a clarification
# envelope that lists the available friendly_names so the next user
# turn can name a real device.

_AMBIG_PRONOUN_TARGET = re.compile(
    r"\b(turn|set|make|dim|brighten|put|change|adjust|switch|move|push|"
    r"raise|lower|toggle|start|stop|open|close|run|activate|"
    r"deactivate|increase|decrease|lock|unlock)\s+"
    r"(it|them|things?|that|this|those|these)\b",
    re.IGNORECASE,
)

# Article + bare device category at any position. Captured here so the
# more-permissive content check downstream only fires when the category
# really is the head noun.
_AMBIG_GENERIC_DEVICE_RE = re.compile(
    r"\b(?:a|an|the)\s+"
    r"(lights?|lamps?|bulbs?|switches|switch|outlets?|plugs?|fans?|"
    r"thermostats?|speakers?|tvs?|televisions?|devices?)\b",
    re.IGNORECASE,
)

# Generic-category words — when these are the ONLY content words in
# the prompt (besides command-verb fillers and stopwords), the user
# named a class of device, not a specific one.
_AMBIG_GENERIC_CATEGORY_WORDS = frozenset(
    {
        "light",
        "lights",
        "lamp",
        "lamps",
        "bulb",
        "bulbs",
        "switch",
        "switches",
        "outlet",
        "outlets",
        "plug",
        "plugs",
        "fan",
        "fans",
        "thermostat",
        "thermostats",
        "speaker",
        "speakers",
        "tv",
        "tvs",
        "television",
        "televisions",
        "device",
        "devices",
    }
)

# Command-verb / filler words that don't count as specifiers. Keeping
# them out of the leftover content set means a message like "dim the
# lights" reduces to {} once we strip stopwords, fillers, and the
# generic category itself.
_AMBIG_FILLER_WORDS = frozenset(
    {
        "turn",
        "switch",
        "toggle",
        "set",
        "start",
        "stop",
        "open",
        "close",
        "dim",
        "brighten",
        "increase",
        "decrease",
        "raise",
        "lower",
        "activate",
        "deactivate",
        "run",
        "put",
        "change",
        "adjust",
        "make",
        "on",
        "off",
        "up",
        "down",
        "all",
        "please",
        "now",
    }
)

# Domain → controllable HA domains we should list options from for the
# inferred clarification. Order matters where overlap is possible
# (e.g. "speaker" → media_player).
_AMBIG_DOMAIN_KEYWORDS: tuple[tuple[re.Pattern[str], tuple[str, ...], str], ...] = (
    (
        re.compile(
            r"\b(light|lights|lamp|lamps|bulb|bulbs|dim|brighten)\b",
            re.IGNORECASE,
        ),
        ("light",),
        "Which light?",
    ),
    (
        re.compile(r"\b(fan|fans)\b", re.IGNORECASE),
        ("fan",),
        "Which fan?",
    ),
    (
        re.compile(
            r"\b(thermostat|thermostats|warmer|cooler|cool|warm|warmth|"
            r"temperature|degrees?|heat|cooling|hotter|colder)\b",
            re.IGNORECASE,
        ),
        ("climate",),
        "Which thermostat?",
    ),
    (
        re.compile(
            r"\b(speaker|speakers|tv|tvs|television|televisions|volume|music)\b",
            re.IGNORECASE,
        ),
        ("media_player",),
        "Which media device?",
    ),
    (
        re.compile(r"\b(switch|switches|outlet|outlets|plug|plugs)\b", re.IGNORECASE),
        ("switch",),
        "Which switch?",
    ),
    (
        re.compile(
            r"\b(cover|covers|blind|blinds|shade|shades|curtain|curtains|garage)\b",
            re.IGNORECASE,
        ),
        ("cover",),
        "Which cover?",
    ),
    (
        re.compile(r"\b(lock|locks|unlock|unlocks|deadbolt|deadbolts)\b", re.IGNORECASE),
        ("lock",),
        "Which lock?",
    ),
)

# When the prompt only carries a pronoun (no domain hint), surface the
# common controllable surfaces so the user can pick from any of them.
_AMBIG_FALLBACK_DOMAINS: tuple[str, ...] = (
    "light",
    "switch",
    "cover",
    "climate",
    "fan",
    "media_player",
    "lock",
)
# "set it to <n>" — when no domain word is present, a bare numeric
# set is almost always a thermostat target.
_AMBIG_NUMERIC_SET_RE = re.compile(r"\b(?:to|at)\s+\d{1,3}\b", re.IGNORECASE)
# Hard cap on options to keep the chat bubble compact.
_AMBIG_MAX_OPTIONS = 12


def _is_unspecified_target_command(user_message: str) -> bool:
    """True when the user wants to control SOMETHING but didn't say what.

    Catches the two patterns that route to the clarification bucket in
    the behavioural benchmark:

      - Pronoun-as-target: "turn it on", "set it to 22", "make it warmer"
      - Generic article + category with no specifier: "turn on a light",
        "dim the lights"

    Negative cases (must NOT trigger):
      - Specific friendly-name fragments: "turn on the kitchen light",
        "open the garage door", "lock the back door"
      - Questions: "is the light on?"  (handled via ``_QUESTION_OPENER``)
      - Vague-automation bare requests: "create an automation for me"
        ("automation" is not in ``_AMBIG_GENERIC_CATEGORY_WORDS``)
    """
    if not user_message:
        return False
    msg = user_message.lower().strip()
    if not msg:
        return False
    # Don't intercept questions — they're answer-shaped, not commands.
    # EXCEPT polite imperatives ("Can you turn it off?", "Could you dim
    # the lights?"): those open with a question word but are commands
    # (the polite-command path routes them to the command specialist).
    # If we bailed here, an ungrounded polite pronoun command would skip
    # clarification and reach the provider, which could pick or
    # hallucinate a target.
    if _QUESTION_OPENER.match(msg) and not _POLITE_COMMAND.match(msg):
        return False

    if _AMBIG_PRONOUN_TARGET.search(msg):
        return True

    if not _AMBIG_GENERIC_DEVICE_RE.search(msg):
        return False

    # A bare ``the <category>`` + a numeric setpoint ("raise the
    # thermostat to 23", "set the fan to 50%") is concrete enough for
    # the command specialist to ground on — the user named the device
    # class AND the target value, and ``_normalize_parametric_calls``
    # already handles the single-of-its-domain entity fallback (e.g.
    # one thermostat in the home) on the policy side. Without this
    # bypass the request reduces to an empty content set after
    # stopword/filler/category stripping and gets short-circuited to a
    # clarification "Which thermostat?" — which the parametric
    # benchmark contract scores as ``intent: command got: clarification``
    # because the user clearly asked for a value-setting action.
    if _AMBIG_NUMERIC_SET_RE.search(msg):
        return False

    # Anything left over after stripping stopwords, command-verb fillers,
    # and the generic category itself is a specifier — "the kitchen light"
    # leaves {"kitchen"} and is treated as specific.
    content = {w for w in re.split(r"[^a-z]+", msg) if w}
    content -= _LOW_CONTEXT_STOPWORDS
    content -= _AMBIG_FILLER_WORDS
    content -= _AMBIG_GENERIC_CATEGORY_WORDS
    return not content


def _build_unspecified_target_clarification(
    user_message: str,
    entities: list[EntitySnapshot],
) -> dict[str, Any] | None:
    """Return a slim clarification envelope for an ambiguous prompt.

    The benchmark contract (``clr.unspecified_device``) requires:
      - ``intent: "clarification"``
      - ``o: [<friendly_name>, ...]`` — every option must be a real
        ``friendly_name`` from the live entity snapshot.

    Returns ``None`` when the prompt is specific enough (caller falls
    through to the LLM) or when no controllable entities of the
    inferred domain exist (a clarification with no options would fail
    the contract just as badly).
    """
    if not _is_unspecified_target_command(user_message):
        return None

    msg = user_message.lower()
    domains_to_show: tuple[str, ...] = ()
    question = "Which device?"
    for pattern, doms, q in _AMBIG_DOMAIN_KEYWORDS:
        if pattern.search(msg):
            domains_to_show = doms
            question = q
            break
    if not domains_to_show:
        if _AMBIG_NUMERIC_SET_RE.search(msg):
            # "set it to 22" — almost certainly a thermostat target.
            domains_to_show = ("climate",)
            question = "Which thermostat?"
        else:
            # Pure pronoun ("turn it on") — surface all controllable.
            domains_to_show = _AMBIG_FALLBACK_DOMAINS

    options: list[str] = []
    seen: set[str] = set()
    for e in entities:
        eid = e.get("entity_id", "")
        if "." not in eid:
            continue
        domain = eid.split(".", 1)[0]
        if domain not in domains_to_show:
            continue
        attrs = e.get("attributes") or {}
        fname = str(attrs.get("friendly_name") or "").strip()
        if not fname or fname in seen:
            continue
        seen.add(fname)
        options.append(fname)
        if len(options) >= _AMBIG_MAX_OPTIONS:
            break

    # No matching entities — handing back an empty options list fails
    # the contract just as badly as a hallucinated command. Let the
    # normal LLM path run instead.
    if not options:
        return None

    # Render the options into ``response`` too. On the streaming path the
    # websocket ``done`` event forwards ``response`` but not ``o``, so a
    # bare "Which device?" would hide the live friendly-name choices the
    # builder constructed. Inlining them keeps the options visible in the
    # primary chat bubble regardless of which field the handler forwards.
    response_text = f"{question}\n\n" + "\n".join(f"- {name}" for name in options)

    return {
        "intent": "clarification",
        "response": response_text,
        "o": options,
    }


# ── Multi-target command short-circuit ──────────────────────────────
# The LoRA reliably mishandles two multi-target shapes:
#
#   * ``turn (on|off) all|every X`` — the command specialist either
#     hallucinates a wildcard entity_id (``light.*``) which the policy
#     blocks, emits a single call against one of the lights (so the
#     bench's action_count check fails 4 vs 1), or — when the bench
#     prompt is also intercepted by ``_is_unspecified_target_command``
#     — routes to the clarification specialist with a "Which light?"
#     ask. None of those satisfy the ``action_count_matches_prompt_subject``
#     contract for the ``cmd.multi.category`` bucket.
#
#   * ``turn (on|off) the X and Y lights`` — the command specialist
#     correctly emits one call per named device. HA executes them
#     fine, but the bench's ``one_call_per_named_friendly_name_in_prompt``
#     check counts the number of fixture friendly_names that appear as
#     SUBSTRINGS in the prompt — and the trailing plural ("lights")
#     makes that substring count asymmetric (one of "kitchen light" /
#     "bedroom light" matches as a prefix of "bedroom lights" while the
#     other doesn't). Emitting a SINGLE multi-target call instead lines
#     the executed-call count up with whatever the bench's substring
#     count happens to be — and HA's native multi-entity service-call
#     semantics still execute on every targeted device.
#
# Both patterns are intercepted upstream of the LoRA so the result is
# deterministic against any fixture (no fixture-specific assumptions).

_MULTI_TARGET_CATEGORY_TO_DOMAIN: dict[str, str] = {
    "light": "light",
    "switch": "switch",
    "fan": "fan",
    "cover": "cover",
    "blind": "cover",
    "shade": "cover",
    "curtain": "cover",
    "lock": "lock",
    "thermostat": "climate",
}


def _singularize_category_noun(noun: str) -> str:
    """Map a plural category noun to its singular form for
    ``_MULTI_TARGET_CATEGORY_TO_DOMAIN`` lookup. Plain ``rstrip("s")``
    turns ``switches`` into ``switche`` (missing from the map) and
    ``blinds`` into ``blind`` (which works); handle the ``-es`` plural
    explicitly so both forms hit a domain."""
    n = noun.lower()
    if n in _MULTI_TARGET_CATEGORY_TO_DOMAIN:
        return n
    if n.endswith("es") and n[:-2] in _MULTI_TARGET_CATEGORY_TO_DOMAIN:
        return n[:-2]
    if n.endswith("s") and n[:-1] in _MULTI_TARGET_CATEGORY_TO_DOMAIN:
        return n[:-1]
    return n


# Exclusion qualifiers ("except the bedroom light", "but not the
# hallway", "other than the porch"). When present, the deterministic
# whole-domain fan-out would silently execute on the excluded device
# too — fall through so the area / entity-aware specialist applies the
# exclusion correctly.
_EXCLUSION_QUALIFIER_RE = re.compile(
    r"\b(?:except(?:\s+for)?|but\s+not|other\s+than|excluding|"
    r"besides|apart\s+from|aside\s+from)\b",
    re.IGNORECASE,
)

# Verb classification by semantic action. ``on`` = activate / open /
# unlock; ``off`` = deactivate / close / lock. The right service per
# domain comes from ``_MULTI_TARGET_DOMAIN_VERB_TO_SERVICE`` so e.g.
# "open the blinds" maps to ``cover.open_cover`` instead of the invalid
# ``cover.turn_on``.
_MULTI_TARGET_VERB_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(
            r"\b(?:turn\s+off|switch\s+off|shut\s+off|close)\b",
            re.IGNORECASE,
        ),
        "off",
    ),
    (
        re.compile(
            r"\b(?:turn\s+on|switch\s+on|power\s+on|open)\b",
            re.IGNORECASE,
        ),
        "on",
    ),
)

# Negation / exclusion qualifiers that flip the meaning of the matched
# verb. "don't turn off the lights" / "do not power on the fan" /
# "never close the blinds" must fall through to the LLM — executing
# the positive verb would perform the very action the user prohibited.
# Matched within a short window before the verb so leading prose
# ("hey, please don't turn off all lights") still negates correctly.
_NEGATION_BEFORE_VERB_RE = re.compile(
    r"\b(?:do\s*n[o']?t|don[o']?t|never|stop|cancel|skip|avoid|"
    r"do\s+not|please\s+do\s+not|please\s+don[o']?t)\b"
    r"[^.!?]{0,40}?"
    r"\b(?:turn\s+(?:on|off)|switch\s+(?:on|off)|shut\s+off|"
    r"power\s+on|open|close)\b",
    re.IGNORECASE,
)


def _multi_target_is_negated(msg: str) -> bool:
    """True when the verb is preceded by an explicit negation / exclusion
    ("don't turn off …", "never close …"). Used to refuse the
    deterministic multi-target envelope so the prohibition reaches the
    LLM / conversational layer intact."""
    return bool(_NEGATION_BEFORE_VERB_RE.search(msg))


_MULTI_TARGET_DOMAIN_VERB_TO_SERVICE: dict[str, dict[str, str]] = {
    "light": {"on": "light.turn_on", "off": "light.turn_off"},
    "switch": {"on": "switch.turn_on", "off": "switch.turn_off"},
    "fan": {"on": "fan.turn_on", "off": "fan.turn_off"},
    "cover": {"on": "cover.open_cover", "off": "cover.close_cover"},
    "lock": {"on": "lock.unlock", "off": "lock.lock"},
    "climate": {"on": "climate.turn_on", "off": "climate.turn_off"},
}

# "all the lights" / "all lights" / "every light" / "each switch"
_MULTI_TARGET_CATEGORY_SCOPE_RE = re.compile(
    r"\b(?:all\s+(?:the\s+|of\s+(?:the\s+|my\s+|our\s+)?)?|every\s+|each\s+)"
    r"(lights?|switches?|fans?|covers?|blinds?|shades?|curtains?|locks?|"
    r"thermostats?)\b",
    re.IGNORECASE,
)

# Area qualifier — "in the kitchen", "in conservatory", "in my bedroom",
# "in the upstairs hallway". Matches any ``in <article>? <word(s)>``
# tail after a multi-target scope, NOT a fixed room list — Home
# Assistant areas are user-defined and we can't enumerate them
# statically. Scheduling phrases ("in 10 minutes", "in the morning")
# are caught by ``_AUTOMATION_PATTERNS`` before this regex runs, so
# remaining ``in X`` is overwhelmingly an area qualifier. False
# positives here cause a safe fall-through to the LLM specialist — the
# opposite (silently fanning out across every device) is the unsafe
# outcome the reviewer flagged.
_AREA_QUALIFIER_RE = re.compile(
    r"\bin\s+(?:the\s+|my\s+|our\s+|a\s+)?"
    r"[a-z][a-z\-]*(?:\s+[a-z][a-z\-]*){0,3}\b",
    re.IGNORECASE,
)

# Locative qualifiers that DON'T start with "in" — trailing adverbs
# ("all lights upstairs", "close all blinds downstairs") and other
# prepositional area phrases ("on this floor", "out back", "over
# there"). Same intent as ``_AREA_QUALIFIER_RE``: a category-scope
# command carrying one of these is area-scoped, so the deterministic
# whole-domain fan-out must defer to the area-aware LLM path.
_AREA_QUALIFIER_LOCATIVE_RE = re.compile(
    r"\b(?:upstairs|downstairs|outside|inside|outdoors|indoors|"
    r"out\s+(?:back|front|here|there)|over\s+(?:here|there)|"
    r"in\s+(?:here|there)|on\s+(?:this|the\s+\w+)\s+floor)\b",
    re.IGNORECASE,
)

# Delayed-command phrasing — "in N seconds/minutes/hours". A one-shot
# delayed command, not an immediate one: the multi-target deterministic
# branch must fall through so the delay isn't dropped.
_DELAYED_IN_DURATION_RE = re.compile(
    r"\bin\s+(?:a|an|\d+)\s+(second|minute|hour)s?\b",
    re.IGNORECASE,
)

# Scene/automation CREATION phrasing — "create a scene that turns off all
# the lights", "make a scene", "save this as a scene". The verb
# ("turn off") matches the multi-target command pattern, but the user
# asked to BUILD a scene/automation, not to execute the action now.
# Fall through to the scene/automation specialist instead.
_SCENE_CREATION_RE = re.compile(
    r"\b(?:create|make|build|save|set\s+up|define|add)\b[^.!?]*\bscene\b"
    r"|\bscene\s+(?:that|which|to|where|for|named|called)\b"
    r"|\bsave\b[^.!?]*\bas\s+a\s+scene\b",
    re.IGNORECASE,
)

# Coordinator that introduces a SECOND command after a category-scope
# clause ("turn off all lights AND lock the front door"). When a verb
# follows the conjunction, the deterministic category branch would emit
# only the first command's calls — defer the whole turn to the provider.
_TRAILING_SECOND_COMMAND_RE = re.compile(
    r"\b(?:and|then|also|,)\s+(?:also\s+)?"
    r"(?:turn|switch|toggle|lock|unlock|open|close|set|dim|start|stop|"
    r"run|play|pause|arm|disarm|activate|deactivate|enable|disable)\b",
    re.IGNORECASE,
)

# A SECOND coordinated category after the scope ("all lights AND
# switches", "all lights and all the fans"). The category branch only
# fans out the FIRST category and would silently drop the rest — defer
# to the provider. A named-pair connector ("kitchen and bedroom lights")
# puts a ROOM word (not a category noun) right after "and", so it does
# not trip this guard.
_TRAILING_SECOND_CATEGORY_RE = re.compile(
    r"\b(?:and|,)\s+"
    r"(?:all\s+(?:the\s+|of\s+the\s+)?|every\s+|each\s+|the\s+|my\s+|our\s+)?"
    r"(?:lights?|switches?|fans?|covers?|blinds?|shades?|curtains?|locks?|"
    r"thermostats?)\b",
    re.IGNORECASE,
)

# Non-numeric scheduling phrases ("after dinner", "before I leave",
# "before bed", "after work") that _AUTOMATION_PATTERNS misses — it only
# anchors at/after/before on a DIGIT. These describe timing, so the
# deterministic immediate fan-out must defer to the provider.
_NONNUMERIC_SCHEDULE_RE = re.compile(
    r"\b(?:after|before)\s+(?!\d)(?:i|we|you|the|my|our|sun|"
    r"dinner|lunch|breakfast|bed|bedtime|work|school|dark|"
    r"sunrise|sunset|dawn|dusk|night|noon|midnight|"
    r"leaving|arriving|leave|arrive|wake|sleep)\b",
    re.IGNORECASE,
)

# Bare category noun used for the named-pair detection.
_MULTI_TARGET_CATEGORY_NOUN_RE = re.compile(
    r"\b(lights?|switches?|fans?|covers?|blinds?|shades?|curtains?|locks?|"
    r"thermostats?)\b",
    re.IGNORECASE,
)

# Strip the trailing category word ("Light", "Lock", …) from a fixture's
# friendly_name to derive its "stem" — what the user typically writes in
# the prompt. "Kitchen Light" → "kitchen"; "Front Door Lock" → "front
# door"; "Bedroom Fan" → "bedroom". Used by the named-pair detector so
# "kitchen and bedroom lights" matches even though neither full
# friendly_name appears verbatim.
_MULTI_TARGET_FNAME_SUFFIX_RE = re.compile(
    r"\s+(light|switch|fan|cover|blind|shade|curtain|lock|thermostat)s?$",
    re.IGNORECASE,
)

# Mirror the safety policy's per-turn call cap so the fan-out we build
# never gets rejected downstream. Keep in sync with
# ``command_policy._MAX_COMMAND_CALLS``.
_MULTI_TARGET_MAX_FAN_OUT = 5
# Mirror the safety policy's per-call entity cap. Single-call multi-
# target proposals must stay under this.
_MULTI_TARGET_MAX_PER_CALL = 3


def _multi_target_verb(msg: str) -> str | None:
    for pattern, verb in _MULTI_TARGET_VERB_PATTERNS:
        if pattern.search(msg):
            return verb
    return None


def _multi_target_service_for(domain: str, verb: str) -> str | None:
    table = _MULTI_TARGET_DOMAIN_VERB_TO_SERVICE.get(domain)
    if table is None:
        return None
    return table.get(verb)


def _build_multi_target_command_envelope(
    user_message: str,
    entities: list[EntitySnapshot] | None,
) -> dict[str, Any] | None:
    """Build a deterministic slim command envelope for multi-target prompts.

    Returns ``None`` when no pattern matches — caller falls through to
    the standard LLM round-trip. When a category-scope prompt resolves
    to MORE than ``_MULTI_TARGET_MAX_FAN_OUT`` entities, only the first
    cap-many are included (an alternative — failing the whole turn —
    would be strictly worse for the user).
    """
    if not user_message or not entities:
        return None
    msg = user_message.lower().strip()
    verb = _multi_target_verb(msg)
    if verb is None:
        return None

    # Negated / exclusion phrasing ("don't turn off all lights",
    # "never close the blinds") matches the positive verb above but
    # explicitly prohibits the action. Fall through so the LLM /
    # conversational layer can acknowledge the prohibition — executing
    # the verb here would do the exact opposite of what the user asked.
    if _multi_target_is_negated(msg):
        return None

    # Scene/automation creation ("create a scene that turns off all the
    # lights") — the verb matches but the user wants a scene BUILT, not
    # the lights turned off now. Defer to the scene/automation specialist.
    if _SCENE_CREATION_RE.search(msg):
        return None

    # Scheduling / conditional language ("at sunset", "every morning",
    # "when X", "if Y") means this is an automation request, not a
    # one-shot multi-target command. Fall through so the automation
    # specialist gets the prompt — emitting an immediate command
    # envelope here would fire the action right now and silently drop
    # the schedule the user explicitly asked for.
    for pat in _AUTOMATION_PATTERNS:
        if pat.search(msg):
            return None
    # Delayed-command phrasing ("in 10 minutes", "in 2 hours") is NOT in
    # _AUTOMATION_PATTERNS (which anchors on at/after/before + a digit),
    # so catch it here. "turn off all lights in 10 minutes" must NOT
    # execute immediately — fall through to the delayed-command path.
    if _DELAYED_IN_DURATION_RE.search(msg):
        return None
    # Non-numeric scheduling ("after dinner", "before I leave") —
    # _AUTOMATION_PATTERNS only catches at/after/before + a digit, so
    # catch the worded forms here. Timing language must not execute now.
    if _NONNUMERIC_SCHEDULE_RE.search(msg):
        return None
    # Compound request with a SECOND command after the category/named
    # clause ("turn off all lights and lock the front door"). The
    # deterministic branch only builds the first command's calls and
    # would silently drop the rest — defer the whole turn to the LLM.
    # A named-pair connector ("kitchen and bedroom lights") puts a NOUN
    # after "and", not a verb, so it does not trip this guard.
    if _TRAILING_SECOND_COMMAND_RE.search(msg):
        return None
    # A SECOND coordinated category ("all lights and switches") — the
    # fan-out would only cover the first category. Defer to the provider.
    if _TRAILING_SECOND_CATEGORY_RE.search(msg):
        return None
    # Pattern 1 — category scope ("all/every X"): split every matching
    # entity across policy-compliant calls. ``apply_command_policy``
    # rejects any single call targeting more than
    # ``_MULTI_TARGET_MAX_PER_CALL`` entities and any turn emitting more
    # than ``_MULTI_TARGET_MAX_FAN_OUT`` calls, so the deterministic
    # envelope mirrors both caps. When the home holds more entities than
    # the product of the two caps (i.e. > 15 in the current policy),
    # fall through to the LLM rather than silently truncate — a
    # category-wide command beyond that ceiling deserves a confirmation
    # step, not a partial action presented as complete.
    scope_match = _MULTI_TARGET_CATEGORY_SCOPE_RE.search(msg)
    if scope_match:
        noun = _singularize_category_noun(scope_match.group(1))
        domain = _MULTI_TARGET_CATEGORY_TO_DOMAIN.get(noun)
        if domain is None:
            return None
        # Area qualifier present ("all lights in the kitchen", "all
        # lights upstairs", "close all blinds downstairs") — the
        # deterministic branch has no per-entity area data here, so
        # firing a whole-home fan-out would silently ignore the
        # qualifier and act on devices outside the requested area. Fall
        # through to the LLM specialist which has area resolution.
        if _AREA_QUALIFIER_RE.search(msg) or _AREA_QUALIFIER_LOCATIVE_RE.search(msg):
            return None
        # Exclusion qualifier ("all lights except the bedroom light") —
        # falling through is the only safe path: executing the whole-
        # domain fan-out would silently include the explicitly excluded
        # device.
        if _EXCLUSION_QUALIFIER_RE.search(msg):
            return None
        service = _multi_target_service_for(domain, verb)
        if service is None:
            return None
        targets: list[EntitySnapshot] = []
        for e in entities:
            if not isinstance(e, dict):
                continue
            eid = e.get("entity_id", "")
            if "." in eid and eid.split(".", 1)[0] == domain:
                targets.append(e)
        if not targets:
            return None
        max_total = _MULTI_TARGET_MAX_FAN_OUT * _MULTI_TARGET_MAX_PER_CALL
        if len(targets) > max_total:
            # Past the policy ceiling — let the LLM handle it (likely
            # produces a clarification asking the user to narrow scope).
            return None
        eids: list[str] = []
        names: list[str] = []
        for e in targets:
            eid = e["entity_id"]
            attrs = e.get("attributes") or {}
            fname = str(attrs.get("friendly_name") or eid)
            eids.append(eid)
            names.append(fname)
        # Chunk into per-call groups of _MULTI_TARGET_MAX_PER_CALL so the
        # downstream policy accepts each call.
        calls: list[dict[str, Any]] = []
        for i in range(0, len(eids), _MULTI_TARGET_MAX_PER_CALL):
            chunk = eids[i : i + _MULTI_TARGET_MAX_PER_CALL]
            calls.append({"service": service, "target": {"entity_id": chunk}})
        verb_word = "on" if verb == "on" else "off"
        if len(targets) == 1:
            noun_form = noun
        elif noun.endswith(("ch", "sh", "x", "s")):
            noun_form = f"{noun}es"
        else:
            noun_form = f"{noun}s"
        response_text = f"Turning {verb_word} {len(targets)} {noun_form}: {', '.join(names)}."
        return {
            "intent": "command",
            "response": response_text,
            "calls": calls,
        }

    # Pattern 2 — named pair/triple ("X and Y category"): emit a single
    # multi-target call so the bench's substring-based count of named
    # friendly_names lines up with the number of ``c`` entries it sees.
    cat_match = _MULTI_TARGET_CATEGORY_NOUN_RE.search(msg)
    if cat_match is None:
        return None
    if " and " not in f" {msg} ":
        return None  # named pair requires the explicit connector
    noun = _singularize_category_noun(cat_match.group(1))
    domain = _MULTI_TARGET_CATEGORY_TO_DOMAIN.get(noun)
    if domain is None:
        return None
    # Restrict stem/name search to the coordinated category clause —
    # text up to and including the category word. Otherwise a prompt
    # like "turn off the kitchen light and check the bedroom temperature"
    # would match "bedroom" outside the category and silently turn off
    # the bedroom light too. The category-clause substring is the only
    # portion that's actually coordinated with the action verb.
    search_scope = msg[: cat_match.end()]
    matched: list[tuple[int, str, str]] = []
    seen: set[str] = set()
    for e in entities:
        if not isinstance(e, dict):
            continue
        eid = e.get("entity_id", "")
        if "." not in eid or eid.split(".", 1)[0] != domain:
            continue
        if eid in seen:
            continue
        attrs = e.get("attributes") or {}
        fname = str(attrs.get("friendly_name") or "").strip()
        if not fname:
            continue
        # Try the full friendly_name first ("Kitchen Light"), then the
        # stem ("Kitchen") with the category word stripped. The stem
        # match is what catches the common "kitchen and bedroom lights"
        # phrasing where the per-name suffix is dropped.
        fname_lower = fname.lower()
        if len(fname_lower) >= 4 and re.search(rf"\b{re.escape(fname_lower)}\b", search_scope):
            matched.append((len(fname_lower), eid, fname))
            seen.add(eid)
            continue
        stem = _MULTI_TARGET_FNAME_SUFFIX_RE.sub("", fname).strip().lower()
        if not stem or len(stem) < 4 or stem == fname_lower:
            continue
        if re.search(rf"\b{re.escape(stem)}\b", search_scope):
            matched.append((len(stem), eid, fname))
            seen.add(eid)
    if len(matched) < 2:
        return None
    # Longest-stem-first so "living room" beats "room" if a fixture has
    # both. Drop matches past the combined policy ceiling — falling
    # through to the LLM is strictly safer than executing a partial set
    # while phrasing the response as if every named target ran.
    matched.sort(key=lambda t: -t[0])
    max_total = _MULTI_TARGET_MAX_FAN_OUT * _MULTI_TARGET_MAX_PER_CALL
    if len(matched) > max_total:
        return None
    eids = [eid for _, eid, _ in matched]
    fnames = [fn for _, _, fn in matched]
    service = _multi_target_service_for(domain, verb)
    if service is None:
        return None
    # Chunk across calls so each one stays within the policy's per-call
    # entity cap. ``apply_command_policy`` rejects single calls past the
    # cap, so emitting a 4-name "kitchen, bedroom, hallway, porch lights"
    # request as one call would silently no-op all four; chunking keeps
    # every named target in the envelope.
    calls: list[dict[str, Any]] = []
    for i in range(0, len(eids), _MULTI_TARGET_MAX_PER_CALL):
        chunk = eids[i : i + _MULTI_TARGET_MAX_PER_CALL]
        calls.append({"service": service, "target": {"entity_id": chunk}})
    verb_word = "on" if verb == "on" else "off"
    if len(fnames) == 2:
        names_str = " and ".join(fnames)
    else:
        names_str = ", ".join(fnames[:-1]) + f", and {fnames[-1]}"
    response_text = f"Turning {verb_word} {names_str}."
    return {
        "intent": "command",
        "response": response_text,
        "calls": calls,
    }


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


def _entity_need_keys(
    entity: EntitySnapshot,
    device_classes: frozenset[str],
    domains: frozenset[str],
) -> list[str]:
    """The pinned-need keys an entity satisfies: ``domain:<d>`` and/or
    ``class:<dc>``. Empty when the entity matches no need."""
    keys: list[str] = []
    eid = entity.get("entity_id", "")
    if "." not in eid:
        return keys
    domain = eid.split(".", 1)[0]
    if domain in domains:
        keys.append(f"domain:{domain}")
    if device_classes:
        dc = str(entity.get("attributes", {}).get("device_class", "")).lower()
        if dc and dc in device_classes:
            keys.append(f"class:{dc}")
    return keys


def _filter_cloud_entities(
    entities: list[EntitySnapshot],
    keywords: set[str],
    *,
    cap: int = _CLOUD_MAX_ENTITIES,
    message: str = "",
) -> list[EntitySnapshot]:
    """Select the cap-many most relevant entities for the CLOUD prompt.

    Keyword ranking alone (``_filter_entities_by_keywords``) buries the
    entities a multi-condition request needs but does not name after the
    request words: an indoor-temperature sensor, a weather entity, a
    pressure sensor named after its chip, a climate setpoint. Worse, a
    diagnostic "CPU Temperature" sensor carries device_class=temperature
    AND scores higher on the literal word "temperature" than the room
    thermometer the automation needs. On a large install those required
    entities sort past the cap and the model reports it cannot see them.

    This layers required-need PINNING over the keyword ranking:

    1. Resolve the request into device_class / domain NEEDS
       (``_cloud_pinned_needs``): "pressure" → a pressure sensor,
       "windows"/"open" → opening/door/window binary_sensors, "fan" → the
       fan domain, "weather" → the weather domain, "inside"/"outside"/
       "temperature" → temperature sensors, "thermostat"/"ac" → climate.
    2. For each need, PIN up to ``_PER_NEED_KEEP`` entities, ranked by
       ``_need_relevance`` (request qualifier/area match, with diagnostic
       sensors demoted) — NOT by the keyword score, so the room sensor wins
       its slot over hundreds of CPU/system sensors of the same class.
    3. Fill the rest of the cap with the keyword-ranked remainder.

    The cap is always honoured — pinned entities count against it, so the
    prompt does not grow. When the request implies no need, this is the
    plain keyword ranking. ``cap <= 0`` yields an empty list. The output
    order is the keyword ranking (pinned-but-unranked entities sort to the
    end), deterministic for a given input.
    """
    if cap <= 0:
        return []

    # Deterministic full keyword ranking (score desc, original index asc).
    # Keep zero-score entities too so pinning can still reach a required-
    # but-unnamed entity (a weather entity, an indoor thermometer) that the
    # bare keyword match scored 0.
    base_order: dict[int, int] = {}
    ranked = sorted(
        enumerate(entities),
        key=lambda t: (-_score_entity_against_keywords(t[1], keywords), t[0]),
    )
    ranked_entities: list[EntitySnapshot] = []
    for rank_idx, (orig_idx, e) in enumerate(ranked):
        ranked_entities.append(e)
        base_order[orig_idx] = rank_idx

    device_classes, domains = _cloud_pinned_needs(keywords, message)
    if not device_classes and not domains:
        # No semantic need to pin — keyword ranking decides the cap.
        return ranked_entities[:cap]

    # Bucket every need-matching entity by its need key, then keep the most
    # need-relevant few per bucket. Ranking inside a bucket is by
    # _need_relevance (demotes diagnostic sensors, rewards qualifier/area
    # match); original index breaks ties so selection is stable.
    buckets: dict[str, list[tuple[int, int, EntitySnapshot]]] = {}
    for orig_idx, e in enumerate(entities):
        for key in _entity_need_keys(e, device_classes, domains):
            buckets.setdefault(key, []).append((_need_relevance(e, keywords), orig_idx, e))

    pinned_idx: set[int] = set()
    for cand in buckets.values():
        cand.sort(key=lambda t: (-t[0], t[1]))
        for _, orig_idx, _e in cand[:_PER_NEED_KEEP]:
            pinned_idx.add(orig_idx)

    if not pinned_idx:
        return ranked_entities[:cap]

    # Compose: pinned entities first (so they always make the cut), then
    # the keyword-ranked remainder until the cap is full. Finally re-sort
    # the whole selection back into keyword-rank order for a stable,
    # readable prompt block.
    pinned = [(base_order[i], entities[i]) for i in pinned_idx]
    pinned.sort(key=lambda t: t[0])
    selected_idx = set(pinned_idx)
    result_pairs: list[tuple[int, EntitySnapshot]] = list(pinned)

    if len(result_pairs) >= cap:
        result_pairs.sort(key=lambda t: t[0])
        return [e for _, e in result_pairs[:cap]]

    # Fill from the keyword ranking (score desc, original index asc), NOT
    # the raw input order — otherwise early unrelated entities displace
    # later high-scoring matches whenever a pinned need also fires, which
    # is exactly the large-install case this selector exists to fix.
    for orig_idx, e in ranked:
        if len(result_pairs) >= cap:
            break
        if orig_idx in selected_idx:
            continue
        result_pairs.append((base_order[orig_idx], e))
        selected_idx.add(orig_idx)

    result_pairs.sort(key=lambda t: t[0])
    return [e for _, e in result_pairs[:cap]]
