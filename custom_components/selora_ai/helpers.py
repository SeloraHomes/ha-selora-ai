"""Shared utility functions used across the Selora AI integration.

Consolidates duplicated helpers that previously existed in multiple modules
(__init__, mcp_server, llm_client, collector, automation_utils).
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from .automation_store import AutomationStore
    from .scene_store import SceneStore

from .const import AUTOMATION_ID_PREFIX, DOMAIN

_LOGGER = logging.getLogger(__name__)


# ── Text sanitisation ────────────────────────────────────────────────────────


def sanitize_untrusted_text(value: object, limit: int = 200) -> str:
    """Normalize and truncate untrusted string fields.

    Prevents prompt-injection via entity friendly names, automation aliases,
    or other user-controlled strings that flow into LLM prompts or MCP responses.
    """
    text = " ".join(str(value or "").split())
    if len(text) > limit:
        text = text[: limit - 3] + "..."
    return text


def format_untrusted_text(value: object) -> str:
    """Render untrusted metadata as a JSON-quoted data value."""
    return json.dumps(sanitize_untrusted_text(value, limit=160), ensure_ascii=True)


# ── Entity state formatting ─────────────────────────────────────────────────


def format_entity_state(value: str) -> str:
    """Convert ISO 8601 timestamps to 12-hour AM/PM format.

    Non-timestamp values are returned stripped of surrounding whitespace.
    """
    from datetime import datetime

    stripped = value.strip()
    for fmt in (
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S.%f%z",
        "%Y-%m-%dT%H:%M:%S",
    ):
        try:
            dt = datetime.strptime(stripped, fmt)
            return dt.strftime("%I:%M %p").lstrip("0")
        except ValueError:
            continue
    return stripped


# ── Entity ID extraction ────────────────────────────────────────────────────


def collect_entity_ids(value: Any) -> set[str]:
    """Recursively extract entity_id values from any nested config structure.

    Works on automation configs, trigger/action/condition dicts, and arbitrary
    nested structures containing ``entity_id`` keys.
    """
    found: set[str] = set()

    def _walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, child in node.items():
                if key == "entity_id":
                    if isinstance(child, str):
                        found.add(child)
                    elif isinstance(child, list):
                        for item in child:
                            if isinstance(item, str):
                                found.add(item)
                else:
                    _walk(child)
        elif isinstance(node, list):
            for item in node:
                _walk(item)

    _walk(value)
    return found


# ── Selora automation identification ────────────────────────────────────────


def is_selora_automation(automation: dict[str, Any]) -> bool:
    """Return True if this automation was created by Selora AI."""
    aid = str(automation.get("id", ""))
    desc = str(automation.get("description", ""))
    alias = str(automation.get("alias", ""))
    return (
        aid.startswith(AUTOMATION_ID_PREFIX)
        or "[Selora AI]" in desc
        or alias.startswith("[Selora AI]")
    )


# ── AutomationStore singleton ──────────────────────────────────────────────


def get_automation_store(hass: HomeAssistant) -> AutomationStore:
    """Return (or lazily create) the AutomationStore from hass.data."""
    from .automation_store import AutomationStore

    domain_data = hass.data.setdefault(DOMAIN, {})
    if "_automation_store" not in domain_data:
        domain_data["_automation_store"] = AutomationStore(hass)
    return domain_data["_automation_store"]


def get_scene_store(hass: HomeAssistant) -> SceneStore:
    """Return (or lazily create) the SceneStore from hass.data."""
    from .scene_store import SceneStore

    domain_data = hass.data.setdefault(DOMAIN, {})
    if "_scene_store" not in domain_data:
        domain_data["_scene_store"] = SceneStore(hass)
    return domain_data["_scene_store"]
