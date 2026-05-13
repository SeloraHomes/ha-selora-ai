"""Sanitisation and formatting of untrusted entity data for prompts."""

from __future__ import annotations

from ..const import ENTITY_SNAPSHOT_ATTRS
from ..types import EntitySnapshot

_UNTRUSTED_TEXT_LIMIT = 160


def _sanitize_untrusted_text(value: object) -> str:
    """Normalize untrusted metadata before it is shown to the model."""
    from ..helpers import sanitize_untrusted_text

    return sanitize_untrusted_text(value, limit=_UNTRUSTED_TEXT_LIMIT)


def _format_untrusted_text(value: object) -> str:
    """Render untrusted metadata as a quoted data value."""
    from ..helpers import format_untrusted_text

    return format_untrusted_text(value)


def _format_entity_line(entity: EntitySnapshot) -> str:
    """Serialize an entity snapshot into a prompt line with whitelisted attributes."""
    eid = entity.get("entity_id", "")
    state = _format_untrusted_text(entity.get("state", "unknown"))
    attrs = entity.get("attributes", {})
    friendly = _format_untrusted_text(attrs.get("friendly_name", eid))
    parts = [f"entity_id={eid}", f"state={state}", f"friendly_name={friendly}"]
    area = entity.get("area_name", "")
    if area:
        parts.append(f"area={_format_untrusted_text(area)}")
    for key in sorted(ENTITY_SNAPSHOT_ATTRS):
        val = attrs.get(key)
        if val is not None:
            parts.append(f"{key}={_format_untrusted_text(val) if isinstance(val, str) else val}")
    return "  - " + "; ".join(parts)
