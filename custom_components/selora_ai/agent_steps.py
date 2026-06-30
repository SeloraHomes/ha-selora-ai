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


# Per-tool (label, mdi-icon) for the read/inspect tools the architect loop
# calls. The icon hints at the *kind of work* (a magnifier for a search, an
# eye for a state read) rather than a generic wrench, so the timeline reads at
# a glance. Co-located with the label so the two never drift. Write tools
# (execute_command / activate_scene) have their own confirmation UI and are
# intentionally absent — the loop does not narrate them as steps.
_TOOL_STEP_INFO: dict[str, tuple[str, str]] = {
    "get_home_snapshot": ("Reviewed your home", "mdi:home-search-outline"),
    "list_devices": ("Listed your devices", "mdi:format-list-bulleted"),
    "get_device": ("Checked device details", "mdi:information-outline"),
    "get_device_triggers": ("Checked available triggers", "mdi:flash-outline"),
    "get_entity_state": ("Read entity state", "mdi:eye-outline"),
    "find_entities_by_area": ("Looked up devices by area", "mdi:floor-plan"),
    "search_entities": ("Searched your entities", "mdi:magnify"),
    "get_entity_history": ("Checked entity history", "mdi:history"),
    "eval_template": ("Evaluated a template", "mdi:code-braces"),
    "validate_action": ("Validated a service call", "mdi:shield-check-outline"),
    "list_dashboards": ("Checked your dashboards", "mdi:view-dashboard-outline"),
    "insert_dashboard_card": ("Updated a dashboard", "mdi:view-dashboard-outline"),
    "discover_network_devices": ("Scanned the network", "mdi:radar"),
    "list_discovered_flows": ("Checked discovered devices", "mdi:devices"),
    "start_device_flow": ("Started device setup", "mdi:plus-network-outline"),
    "accept_device_flow": ("Paired a device", "mdi:check-network-outline"),
    "list_suggestions": ("Reviewed suggestions", "mdi:lightbulb-outline"),
    "accept_suggestion": ("Accepted a suggestion", "mdi:lightbulb-on-outline"),
}
_DEFAULT_TOOL_ICON = "mdi:cog-outline"


def tool_step_label(tool_name: str) -> str:
    """A short, friendly label for a tool call, for the activity timeline."""
    info = _TOOL_STEP_INFO.get(tool_name)
    return info[0] if info else f"Used {tool_name.replace('_', ' ')}"


def tool_step_icon(tool_name: str) -> str:
    """An mdi icon hinting at the kind of work a tool call did."""
    info = _TOOL_STEP_INFO.get(tool_name)
    return info[1] if info else _DEFAULT_TOOL_ICON


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
