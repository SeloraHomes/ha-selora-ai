"""Helpers shared by the MCP tool handlers."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from homeassistant.core import HomeAssistant

from ..const import (
    DOMAIN,
)

if TYPE_CHECKING:
    from .. import ConversationStore
    from ..automation_store import AutomationStore
    from ..llm_client import LLMClient
    from ..types import (
        RiskAssessment,
    )


_LOGGER = logging.getLogger(__name__)


# ── Helpers ────────────────────────────────────────────────────────────────────


def _sanitize(value: Any, limit: int = 200) -> str:
    """Normalize and truncate untrusted string fields before including in responses."""
    from ..helpers import sanitize_untrusted_text

    return sanitize_untrusted_text(value, limit=limit)


def _get_automation_store(hass: HomeAssistant) -> AutomationStore:
    """Return (or lazily create) the AutomationStore singleton."""
    from ..helpers import get_automation_store

    return get_automation_store(hass)


def _get_conv_store(hass: HomeAssistant) -> ConversationStore:
    """Return (or lazily create) the ConversationStore singleton."""
    from .. import ConversationStore

    domain_data = hass.data.setdefault(DOMAIN, {})
    return domain_data.setdefault("_conv_store", ConversationStore(hass))


def _get_llm(hass: HomeAssistant) -> LLMClient | None:
    """Return the LLMClient from the first active LLM config entry, or None."""
    domain_data = hass.data.get(DOMAIN, {})
    for entry in hass.config_entries.async_loaded_entries(DOMAIN):
        entry_data = domain_data.get(entry.entry_id, {})
        llm = entry_data.get("llm")
        if llm is not None:
            return llm
    return None


async def _read_yaml_automations(hass: HomeAssistant) -> list[dict[str, Any]]:
    """Read automations.yaml in an executor thread."""
    from ..automation_utils import _read_automations_yaml

    path = Path(hass.config.config_dir) / "automations.yaml"
    return await hass.async_add_executor_job(_read_automations_yaml, path)


def _is_selora(automation: dict[str, Any]) -> bool:
    """Return True if this automation was created by Selora AI."""
    from ..helpers import is_selora_automation

    return is_selora_automation(automation)


def _is_pending_automation(auto: dict[str, Any], record: dict[str, Any] | None) -> bool:
    """Return True if a Selora automation should be shown as pending."""
    if auto.get("initial_state", True) is not False:
        return False
    if not record:
        return True
    versions = record.get("versions", [])
    if not versions:
        return True
    latest_message = str(versions[-1].get("message", "")).strip().lower()
    return latest_message != "accepted via mcp"


def _resolve_yaml_automation_entity_id(hass: HomeAssistant, entry: dict[str, Any]) -> str | None:
    """Map an ``automations.yaml`` entry to its HA entity_id.

    Honors the entity registry first (id-based unique_id mapping covers
    HA-side renames and collision suffixes) and falls back to
    ``automation.<slug(alias)>`` so id-less yaml entries — which HA still
    loads as automations using the alias slug — are also recognised.
    """
    from homeassistant.helpers import entity_registry as er  # noqa: PLC0415
    from homeassistant.util import slugify  # noqa: PLC0415

    auto_id = entry.get("id")
    alias = entry.get("alias") if isinstance(entry.get("alias"), str) else None

    if isinstance(auto_id, str) and auto_id:
        registry = er.async_get(hass)
        entity_id = registry.async_get_entity_id("automation", "automation", auto_id)
        if entity_id:
            return entity_id
        candidate = f"automation.{auto_id}"
        if hass.states.get(candidate) is not None:
            return candidate

    if alias:
        candidate = f"automation.{slugify(alias)}"
        if hass.states.get(candidate) is not None:
            return candidate
    return None


def _resolve_automation(
    hass: HomeAssistant,
    *,
    automation_id: str = "",
    entity_id: str = "",
) -> tuple[Any | None, str, str]:
    """Resolve an automation to (state, automation_id, entity_id).

    Either *automation_id* (the YAML/storage id from ``state.attributes['id']``)
    or *entity_id* (e.g. ``automation.morning_routine``) may be provided.
    Returns ``(None, "", "")`` when no matching automation is found.
    """
    if entity_id:
        if not entity_id.startswith("automation."):
            return None, "", ""
        state = hass.states.get(entity_id)
        if state is None:
            return None, "", ""
        return state, str(state.attributes.get("id", "")), state.entity_id
    if automation_id:
        for state in hass.states.async_all("automation"):
            if str(state.attributes.get("id", "")) == automation_id:
                return state, automation_id, state.entity_id
    return None, "", ""


# ── Risk assessment sanitizer ─────────────────────────────────────────────────


def _sanitize_risk(risk: RiskAssessment | dict[str, Any]) -> RiskAssessment:
    """Return a copy of a risk_assessment dict with all strings sanitized."""
    return {
        "level": risk.get("level", "normal"),
        "flags": list(risk.get("flags", [])),
        "reasons": [_sanitize(r, limit=300) for r in risk.get("reasons", [])],
        "scrutiny_tags": list(risk.get("scrutiny_tags", [])),
        "summary": _sanitize(risk.get("summary", ""), limit=300),
    }
