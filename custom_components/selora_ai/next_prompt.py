"""Predict the user's next chat message once a turn has ended.

The panel asks after each ``done`` and shows the answer as the empty
composer's placeholder, which Tab accepts. Nothing is stored: the prediction
belongs to the turn on screen and a reopened session shows none.

A wrong prediction costs the user's attention on every turn, so the model
must clear a confidence bar and is asked for nothing at all when the turn
ends on buttons (an approval, a choice) rather than on a message.
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any

from .const import (
    CONF_NEXT_PROMPT_ENABLED,
    LLM_PROVIDER_NONE,
    LLM_PROVIDER_OLLAMA,
    LLM_PROVIDER_SELORA_LOCAL,
)

if TYPE_CHECKING:
    from .types import ChatMessage

# Selora AI Local is trained on JSON output schemas and cannot write free
# prose (see LLMClient.generate_session_title).
_UNSUPPORTED_PROVIDERS = frozenset({LLM_PROVIDER_SELORA_LOCAL, LLM_PROVIDER_NONE})
# Every extra call runs on the user's own hardware, so it is opt-in there.
_OFF_BY_DEFAULT_PROVIDERS = frozenset({LLM_PROVIDER_OLLAMA})

# The model always guesses and this bar decides: given a way to answer
# "nothing", a small model takes it whatever the conversation holds.
# Below this the guess is not shown. The model is asked how likely the user's
# next message means the same thing, and models overstate that, so the bar
# sits well above even odds.
MIN_CONFIDENCE = 0.7
MAX_PROMPT_CHARS = 140

_TRANSCRIPT_MESSAGES = 8
_CONTENT_CHARS = 600
_YAML_CHARS = 1500

_MARKER_RE = re.compile(r"\s*\[\[[^\]]*\]\]")
_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)

_AUTOMATION_STATUS = {
    "pending": "proposed, not saved yet: Accept and Decline buttons are on the card",
    "saved": "accepted and saved",
    "declined": "declined: the user rejected this change and keeps what they had",
    "superseded": "replaced by a later proposal",
    "refining": "loaded for the user to edit",
}
_SCENE_STATUS = {
    "pending": "proposed, not saved yet: Accept and Decline buttons are on the card",
    "saved": "accepted and saved",
    "declined": "declined: the user rejected this change and keeps what they had",
    "refining": "loaded for the user to edit",
}

SYSTEM_PROMPT = (
    "You guess the next message the user of a smart-home assistant for Home "
    "Assistant will type, from the end of their conversation. Your guess "
    "appears greyed out in their empty message box; it is only shown when your "
    "confidence is high, so be honest about it.\n\n"
    "Good guesses come from a loose end the last turn left:\n"
    "- something the change made inaccurate: a name, description or label that "
    "no longer describes what the automation, scene or device now does;\n"
    "- a part of the user's request that is still not done;\n"
    "- a step the assistant's last reply explicitly offered or set up.\n"
    "Name the concrete value when the conversation makes it clear (for example "
    "the new name).\n\n"
    "Accepting, saving, declining, enabling or running a proposed card are "
    "buttons on the card, never messages, so never guess them. A declined card "
    "is the user's decision, not a loose end: never suggest applying it again. "
    "When there is no loose end, still give your best guess but with a low "
    "confidence.\n\n"
    "Write the guess as the user would type it to the assistant: an instruction "
    "or a question, at most 12 words, no greeting, no quotes, no entity ids and "
    "no [[...]] markers. Never ask for something the user already asked for or "
    "the assistant already did.\n\n"
    "Confidence, 0 to 1: how likely the user wants exactly this done next. "
    "Around 0.9 for a clear loose end, below 0.3 when you are guessing.\n\n"
    'Reply with JSON only: {"prompt": "<your guess>", "confidence": <number>}'
)


def next_prompt_available(provider: str) -> bool:
    """Whether ``provider`` can predict at all."""
    return provider not in _UNSUPPORTED_PROVIDERS


def next_prompt_enabled(config_data: dict[str, Any], provider: str) -> bool:
    """The user's choice, or the provider's default when they made none."""
    if not next_prompt_available(provider):
        return False
    stored = config_data.get(CONF_NEXT_PROMPT_ENABLED)
    if isinstance(stored, bool):
        return stored
    return provider not in _OFF_BY_DEFAULT_PROVIDERS


def _clip(text: str, limit: int) -> str:
    text = _MARKER_RE.sub("", text).strip()
    return text if len(text) <= limit else text[:limit].rstrip() + "…"


def _card_lines(message: ChatMessage, newest: bool) -> list[str]:
    lines: list[str] = []
    automation = message.get("automation")
    if automation:
        alias = automation.get("alias") or "(unnamed)"
        status = _AUTOMATION_STATUS.get(message.get("automation_status") or "", "shown")
        lines.append(f'[Automation card "{alias}", {status}]')
        description = automation.get("description")
        if description:
            lines.append(f"[Its description: {_clip(str(description), _CONTENT_CHARS)}]")
        # Only the newest card's body: it is what the next message reacts to,
        # and earlier versions would crowd it out.
        yaml_text = message.get("automation_yaml")
        if newest and yaml_text:
            lines.append(f"```yaml\n{_clip(yaml_text, _YAML_CHARS)}\n```")
    scene = message.get("scene")
    if scene:
        name = scene.get("name") or "(unnamed)"
        status = _SCENE_STATUS.get(message.get("scene_status") or "", "shown")
        lines.append(f'[Scene card "{name}", {status}]')
    calls = message.get("calls")
    if calls:
        ran = ", ".join(str(c.get("service", "")) for c in calls if isinstance(c, dict))
        if ran:
            lines.append(f"[Ran: {ran}]")
    return lines


def build_transcript(messages: list[ChatMessage]) -> str | None:
    """The conversation's end as the predictor reads it, or None to skip.

    None when the session does not end on an assistant reply, or ends on one
    waiting for a button — an approval, choices, or a proposal to accept: the
    user's next move there is a click. Accepting a proposal asks again, from
    its saved state.
    """
    if not messages or messages[-1].get("role") != "assistant":
        return None
    last = messages[-1]
    if (
        last.get("approval_status") == "pending"
        or last.get("quick_actions")
        or last.get("automation_status") == "pending"
        or last.get("scene_status") == "pending"
    ):
        return None
    tail = messages[-_TRANSCRIPT_MESSAGES:]
    lines: list[str] = []
    for index, message in enumerate(tail):
        role = "USER" if message.get("role") == "user" else "ASSISTANT"
        lines.append(f"{role}: {_clip(message.get('content') or '', _CONTENT_CHARS)}")
        lines.extend(_card_lines(message, index == len(tail) - 1))
    return "\n".join(lines)


def parse_prediction(raw: str | None) -> str | None:
    """The predicted message from the model's JSON, or None below the bar."""
    if not raw:
        return None
    match = _JSON_OBJECT_RE.search(raw)
    if match is None:
        return None
    try:
        data = json.loads(match.group(0))
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    confidence = data.get("confidence")
    prompt = data.get("prompt")
    if not isinstance(confidence, int | float) or not isinstance(prompt, str):
        return None
    if confidence < MIN_CONFIDENCE:
        return None
    prompt = " ".join(prompt.split()).strip().strip('"').strip()
    if not prompt or "[[" in prompt or len(prompt) > MAX_PROMPT_CHARS:
        return None
    return prompt
