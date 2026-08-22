"""Agent-activity steps — the "what's happening" timeline shown in the chat.

A *step* is a small structured record describing one thing the assistant did
while answering (read a device, drafted an automation, validated it, corrected
an invalid service, …). Steps are streamed to the panel interleaved with text
tokens and rendered as a PostHog-style activity list above the reply bubble,
so a multi-round, tool-using, self-correcting turn reads as legible progress
instead of a wall of re-narrated prose.

Transport: a step is encoded as a single stream chunk — ``STREAM_STEP_PREFIX``
followed by the step's JSON. The websocket handler detects the prefix, decodes
the step, forwards it as a ``{"type": "step"}`` event, and collects it for the
final ``done`` event and message persistence. Everything else in the stream is
bubble text.
"""

from __future__ import annotations

import json
import logging
from typing import TypedDict

from .const import STREAM_STEP_PREFIX

_LOGGER = logging.getLogger(__name__)


class AgentStep(TypedDict, total=False):
    """One entry in the agent-activity timeline."""

    id: str
    kind: str  # tool | draft | validate | correct | info | done | error
    label: str
    status: str  # active | done | warn | error
    detail: str
    icon: str  # optional mdi icon override (frontend falls back by kind/status)


# Per-tool mdi icon for the read/inspect tools the architect loop calls. The
# icon hints at the *kind of work* (a magnifier for a search, an eye for a
# state read) rather than a generic wrench, so the timeline reads at a glance.
# Write tools (execute_command / activate_scene) have their own confirmation UI
# and are intentionally absent — the loop does not narrate them as steps.
#
# Only icons. The labels used to live here too, hand-written in the past tense
# ("Checked your dashboards"), with `f"Used {tool}"` for anything not listed —
# so a timeline mixed both voices and every new tool needed an entry to avoid
# the ugly one. The name of the tool is what the row is for, and it needs no
# curation.
_TOOL_STEP_ICONS: dict[str, str] = {
    "get_home_snapshot": "mdi:home-search-outline",
    "list_devices": "mdi:format-list-bulleted",
    "get_device": "mdi:information-outline",
    "get_device_triggers": "mdi:flash-outline",
    "get_entity_state": "mdi:eye-outline",
    "find_entities_by_area": "mdi:floor-plan",
    "search_entities": "mdi:magnify",
    "get_entity_history": "mdi:history",
    "eval_template": "mdi:code-braces",
    "validate_action": "mdi:shield-check-outline",
    "list_dashboards": "mdi:view-dashboard-outline",
    "insert_dashboard_card": "mdi:view-dashboard-outline",
    "discover_network_devices": "mdi:radar",
    "list_discovered_flows": "mdi:devices",
    "start_device_flow": "mdi:plus-network-outline",
    "accept_device_flow": "mdi:check-network-outline",
    "list_suggestions": "mdi:lightbulb-outline",
    "accept_suggestion": "mdi:lightbulb-on-outline",
}
_DEFAULT_TOOL_ICON = "mdi:cog-outline"


def tool_step_label(tool_name: str) -> str:
    """The row's label: the tool's own name, read as English.

    Tense-free and uniform. Past tense made each row a small claim about what
    had happened, which is not what a progress list is for, and the "Used …"
    fallback made every uncurated tool read differently from its neighbours in
    the same list.
    """
    return tool_name.replace("_", " ").strip().capitalize()


def tool_step_icon(tool_name: str) -> str:
    """An mdi icon hinting at the kind of work a tool call did."""
    return _TOOL_STEP_ICONS.get(tool_name, _DEFAULT_TOOL_ICON)


def make_step(
    step_id: str,
    kind: str,
    label: str,
    *,
    status: str = "done",
    detail: str | None = None,
    icon: str | None = None,
) -> AgentStep:
    """Build an :class:`AgentStep`. ``status`` defaults to ``done`` since most
    steps are emitted after the action they describe has completed."""
    step: AgentStep = {"id": step_id, "kind": kind, "label": label, "status": status}
    if icon:
        step["icon"] = icon
    if detail:
        step["detail"] = detail
    return step


def encode_step(step: AgentStep) -> str:
    """Encode a step as a single stream chunk (prefix + JSON)."""
    return STREAM_STEP_PREFIX + json.dumps(step)


def is_step_chunk(chunk: str) -> bool:
    """Whether *chunk* is an encoded agent step rather than bubble text."""
    return chunk.startswith(STREAM_STEP_PREFIX)


def decode_step(chunk: str) -> AgentStep | None:
    """Decode a step chunk produced by :func:`encode_step`. Returns ``None`` for
    a malformed payload — a bad step must never break the chat stream."""
    if not is_step_chunk(chunk):
        return None
    payload = chunk[len(STREAM_STEP_PREFIX) :]
    try:
        data = json.loads(payload)
    except ValueError:
        _LOGGER.debug("Discarding malformed agent-step chunk: %r", payload[:120])
        return None
    if not isinstance(data, dict) or "id" not in data or "label" not in data:
        return None
    return data  # type: ignore[return-value]


def encode_tool_step(seq: int, tool_name: str, *, status: str = "done") -> str:
    """Convenience: encode a ``tool``-kind step for the *seq*-th tool call,
    with a label and icon matched to the tool."""
    return encode_step(
        make_step(
            f"tool-{seq}",
            "tool",
            tool_step_label(tool_name),
            status=status,
            icon=tool_step_icon(tool_name),
        )
    )
