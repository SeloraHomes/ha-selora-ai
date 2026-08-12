"""Token budgeting for the entity block of an LLM prompt.

Pure arithmetic — no Home Assistant imports, no provider imports, no I/O —
so every number here can be unit-tested directly.

Why this module exists
----------------------
Every component of a chat request is capped on its own: history by
``client._trim_history_to_budget``, the entity block by
``intent._CLOUD_MAX_ENTITIES``, scene YAML by its own character limit. What
was missing is a ceiling on their *sum*. History is the only component with
any back-pressure against the total, and when the fixed parts alone exceed
its budget it drops to nothing and the request is sent regardless — there
is no mechanism that makes the entity block or the system prompt yield.
Tool schemas, which travel beside the messages rather than inside them, are
counted nowhere at all.

The ceilings themselves are also guesses rather than measurements:
``_PROVIDER_TOKEN_BUDGETS`` and the 500-line entity cap were both sized for
a cloud-scale window, which is the wrong shape for a 1.7B model served
locally on a 4K–16K window. So the components stay individually reasonable
while the payload as a whole drifts past what the backend will accept.

A cap expressed in lines cannot fix that, because the thing that overflows
is the *context window*, measured in tokens, and shared by everything in
the request. These helpers convert a window into a line count so the
elastic part of the prompt can be sized against what the fixed parts have
already spent — one formula covering a 4K hub and a 128K local model.

Callers own the two inputs the module cannot know: how many tokens they
have already committed (``reserved``) and how expensive one of *their*
rendered lines is (``tokens_per_line``). Nothing here renders an entity or
changes the shape of a line — only how many lines are emitted.
"""

from __future__ import annotations

from math import ceil
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

    from ..types import EntitySnapshot

# Characters per token. Deliberately below the ~4.0 that English prose
# averages on a BPE vocabulary, because entity lines are far denser than
# prose: ``entity_id=binary_sensor.front_door_contact;`` splits into many
# subword pieces, and punctuation-heavy ``key=value;`` runs tokenize worse
# than words. Understating chars-per-token OVERSTATES the token count,
# which leaves the budget on the safe side of the window.
#
# Calibration anchor: ``local_model/prompts/automation_system_prompt.txt``
# is 10,352 characters, and the comment at ``selora_local._SELORA_LOCAL_MAX_
# ENTITY_LINES_AUTOMATION`` describes that prompt as "~2500 tokens".
# 10352 / 3.5 = 2958, i.e. this estimate runs ~18% conservative against a
# real tokenizer — the intended direction. A tokenizer dependency would buy
# that 18% back and is not worth the install weight.
_CHARS_PER_TOKEN = 3.5

# One line costs its text plus the newline that separates it from the next.
_NEWLINE_TOKENS = 1

# Assumed cost of one rendered entity line, per format, used when the
# budget has to be computed before anything is rendered. Both figures are
# measured means over representative entities (a bare light, a fully
# attributed light, a temperature sensor, a media player, a contact
# sensor):
#   sanitize._format_entity_line          mean 192 chars -> ~55 tokens
#   selora_local._format_entities_block   mean  86 chars -> ~25 tokens
# The cloud line carries area/platform/manufacturer/model plus whitelisted
# attributes; the local line carries only entity_id/state/friendly_name.
CLOUD_ENTITY_LINE_TOKENS = 55
LOCAL_ENTITY_LINE_TOKENS = 25

# What to assume when the backend has not told us its context window.
# Treating an unknown window as small is a deliberate asymmetry: a
# large-model user who gets a tighter prompt still gets an answer, while a
# large-install user whose request is rejected gets nothing at all. 8192 is
# the common default for a locally served model.
ASSUMED_CONTEXT_WINDOW = 8192

# Tokens held back for the model's own reply. Generous, because the entity
# block is the elastic part of the prompt and overshooting the window is a
# hard failure while emitting a few lines fewer is not.
#
# These mirror the ``max_tokens`` the request will actually declare, and a
# backend that validates prompt + declared completion against its window
# (llama.cpp, and Ollama on top of it) rejects on the DECLARED number, not
# on what the model goes on to emit. Reserving less than was declared is
# therefore a rejection, however short the real reply turns out to be:
#   no tools — ``client.send_request`` / ``send_request_stream``, 1024
#   tools    — ``base.raw_request`` hardcodes 4096 and
#              ``base.raw_request_stream`` defaults to it, and the tool
#              loop (``_send_request_with_tools`` /
#              ``_stream_request_with_tools``) goes through both.
# Changing either ``max_tokens`` means changing the matching constant here.
RESPONSE_HEADROOM_TOKENS = 1024
TOOL_RESPONSE_HEADROOM_TOKENS = 4096

# Tokens one attached image occupies. Like the tool schemas, an image is
# part of the request but part of no string any prompt measurement sees —
# it rides as a base64 block beside the text.
#
# Derivation: the panel downscales every attachment to
# ``const.CHAT_ATTACHMENT_MAX_EDGE_PX`` (1568) on the long edge, and the
# common vision-model estimate is ``width * height / 750`` tokens.
# 1568^2 / 750 = 3279, rounded up. It is a ceiling, not a mean: a square
# image at the max edge is the worst case, anything smaller costs less,
# and over-reserving here costs entity lines while under-reserving costs
# the whole turn. Local vision models generally tokenize images more
# cheaply than this, which is the safe direction.
#
# Not derived from the base64 length: bytes and tokens are unrelated for
# images (a photo and a flat-colour screenshot of identical dimensions
# compress very differently but cost the same to look at).
IMAGE_TOKENS_PER_ATTACHMENT = 3300

# Floors. An empty AVAILABLE ENTITIES block is worse than a short one:
# handing a model no entities makes it invent entity_ids rather than say it
# cannot see any (the same reasoning as the keyword-ranking fallback in
# ``intent._filter_entities_by_keywords``). So the budget never returns
# zero lines while there are entities to show.
MIN_ENTITY_LINES = 15

# Floor for the bounded generic-local (Ollama) path specifically. 60 is not
# a new invention: it is the entity surface ``client`` already hands to
# constrained providers, so the shipped prompts are known to work with it.
BOUNDED_LOCAL_MIN_ENTITY_LINES = 60


def response_headroom(*, tool_tokens: int) -> int:
    """Completion tokens to hold back for the reply.

    ``tool_tokens`` doubles as the "are there tools on this request"
    signal — callers derive it from whether a tool executor survived the
    ``supports_tools`` gate, so a non-zero cost and a tool-bearing request
    are the same condition. That matters because the tool loop declares a
    4x larger ``max_tokens`` than the plain path, and the reservation has
    to match what the request declares rather than what it will use.
    """
    return TOOL_RESPONSE_HEADROOM_TOKENS if tool_tokens > 0 else RESPONSE_HEADROOM_TOKENS


def attachment_tokens(count: int) -> int:
    """Token cost of ``count`` attached images. Zero for a text-only turn."""
    return max(0, count) * IMAGE_TOKENS_PER_ATTACHMENT


def estimate_tokens(text: str) -> int:
    """Estimate the token cost of ``text``.

    Conservative by construction — see ``_CHARS_PER_TOKEN``. Returns 0 for
    empty text so a caller can add this to a running total unconditionally.
    """
    if not text:
        return 0
    return ceil(len(text) / _CHARS_PER_TOKEN)


def estimate_entity_line_tokens(line: str) -> int:
    """Estimate the token cost of one *rendered* entity line.

    Includes the newline that joins it to the rest of the block, so
    ``sum(estimate_entity_line_tokens(l) for l in lines)`` estimates the
    whole block rather than the lines in isolation.
    """
    return estimate_tokens(line) + _NEWLINE_TOKENS


def entity_budget(
    context_window: int | None,
    *,
    reserved: int,
    tokens_per_line: int = CLOUD_ENTITY_LINE_TOKENS,
    minimum: int = MIN_ENTITY_LINES,
) -> int:
    """How many entity lines fit in ``context_window`` after ``reserved``.

    ``context_window``
        The backend's window in tokens, or ``None`` when it is unknown —
        in which case ``ASSUMED_CONTEXT_WINDOW`` is used, i.e. an unknown
        window is treated as a small one.
    ``reserved``
        Tokens the caller has already committed: system prompt, history,
        the user's message, and headroom for the reply.
    ``tokens_per_line``
        Cost of one line in the caller's own render format.
    ``minimum``
        Never return fewer than this many lines.

    Non-positive or nonsensical inputs collapse to ``minimum`` rather than
    raising: this sits on the prompt-building hot path, and a bad number
    from a backend probe must not take chat down.
    """
    window = ASSUMED_CONTEXT_WINDOW if context_window is None else context_window
    if window <= 0 or tokens_per_line <= 0:
        return max(0, minimum)
    usable = window - reserved
    if usable <= 0:
        return max(0, minimum)
    return max(minimum, usable // tokens_per_line)


def fit_lines_to_tokens(
    lines: Sequence[str],
    token_budget: int,
    *,
    minimum: int = 0,
) -> list[str]:
    """Drop trailing lines until the MEASURED block fits ``token_budget``.

    ``entity_budget`` sizes the block before a single line exists, from a
    mean cost per line — so it is an approximation in both directions. A
    home of richly-attributed entities (area + platform + manufacturer +
    model + attributes) renders lines above the mean, and the line cap can
    additionally be pinned by ``_CLOUD_MAX_ENTITIES`` rather than by the
    token formula, in which case no token arithmetic bounds the block at
    all. Either way the rendered block can exceed what was reserved for
    it, and nothing downstream recovers: the history trimmer only drops
    history.

    This is the check against what was actually produced. ``minimum``
    lines are kept regardless, for the same reason ``entity_budget``
    floors — an empty block makes a model invent entity_ids.
    """
    if token_budget <= 0:
        return list(lines[:minimum])
    used = 0
    kept = 0
    for line in lines:
        used += estimate_entity_line_tokens(line)
        if used > token_budget and kept >= minimum:
            break
        kept += 1
    return list(lines[:kept])


def trim_entities_to_budget(
    entities: Sequence[EntitySnapshot],
    budget: int,
) -> list[EntitySnapshot]:
    """Trim an already relevance-ranked entity list to ``budget`` lines.

    Ranking is NOT done here — callers rank with
    ``intent._filter_entities_by_keywords`` (or ``_filter_cloud_entities``,
    which layers need-pinning over the same scorer) and this only applies
    the bound. Keeping the two apart means there is exactly one ranker in
    the codebase and one place that enforces the size of the block.

    A ``budget`` of zero or less yields an empty list.
    """
    if budget <= 0:
        return []
    return list(entities[:budget])
