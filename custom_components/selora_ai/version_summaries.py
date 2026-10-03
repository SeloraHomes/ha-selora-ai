"""Written one-sentence summaries of automation versions.

The structural change list (``automation_changes``) can say WHICH values
moved, but not what an edit means: a restructure that gives the 07:00 trigger
an id, drops Friday and replaces the "otherwise" branch reads, structurally,
as a trigger removed and an identical one added. Only a reader of both
versions can say "now turns the pump on only on Thursdays". So the configured
LLM writes that sentence once, in the background after a version is saved,
and it is stored on the version — the History tab never asks for it.

The model is handed the change list as its facts and the two versions as
context, never asked to find the differences itself: left to compare the
documents, it restated the whole rule and narrated fields that are not edits
("…and the automation is now enabled", from the `initial_state` Selora sets
on accept).

Skipped when there is nothing to say (no behavioural change — a syntax
migration, a description edit or enabling alone never costs a call), on a
provider that cannot write prose, and when the documents are too large to
send. The panel keeps phrasing the change list itself whenever no summary is
stored.
"""

from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING, Any

from homeassistant.exceptions import HomeAssistantError
import yaml

from .const import DOMAIN
from .llm_client.prompts import _LANGUAGE_NAMES

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from .automation_store import AutomationStore
    from .llm_client import LLMClient
    from .types import AutomationVersion, VersionChange

_LOGGER = logging.getLogger(__name__)

# Past this the two documents are not sent: the call would cost more than a
# history line is worth, and the change list still describes the edit.
_MAX_YAML_CHARS = 6000

# Versions summarised per History-tab open, newest first. Older ones follow
# on later opens; a home with long histories does not pay for all at once.
BACKFILL_LIMIT = 5

# Top-level fields that are not how the automation BEHAVES, so the model never
# sees them. `initial_state` is the enabled flag Selora sets on accept, `id` is
# plumbing, and `description` is prose restating the rule — shown, each is
# narrated as though it were the edit.
_NOT_BEHAVIOUR = frozenset({"id", "initial_state", "description"})

_MAX_VALUE_CHARS = 160
_ENTITY_ID_RE = re.compile(r"\b[a-z_]+\.[a-z0-9_]+\b")
_INFLIGHT_KEY = "_version_summaries_inflight"


def summary_language(hass: HomeAssistant) -> str:
    """Return the base language a summary is written in for this home."""
    base = str(hass.config.language or "en").lower().split("-")[0]
    # The prompt only carries a directive for languages it knows; anything
    # else is answered in English, so it is recorded as English.
    return base if base in _LANGUAGE_NAMES else "en"


def _get_llm(hass: HomeAssistant) -> LLMClient | None:
    domain_data = hass.data.get(DOMAIN, {})
    for entry in hass.config_entries.async_loaded_entries(DOMAIN):
        llm = domain_data.get(entry.entry_id, {}).get("llm")
        if llm is not None:
            return llm
    return None


def _needs_summary(version: AutomationVersion) -> bool:
    """Whether a version still has no sentence and has something to say.

    A written summary is permanent. It is never rewritten — not when the
    prompt changes, not when the home's language does — because the cost of
    rewriting is one LLM call per version across every automation, paid for a
    sentence that was already right. A summary in another language than the
    viewer's is simply not shown; the panel phrases the change list instead.
    """
    return bool(version.get("changes")) and not version.get("summary")


def _behaviour(data: Any) -> dict[str, Any] | None:
    if not isinstance(data, dict):
        return None
    return {k: v for k, v in data.items() if k not in _NOT_BEHAVIOUR}


def _document(version: AutomationVersion) -> str:
    """The version as the model is shown it: behaviour only."""
    kept = _behaviour(version.get("data"))
    if not kept:
        # A version stored without its data: the YAML is all there is.
        try:
            kept = _behaviour(yaml.safe_load(version.get("yaml") or ""))
        except yaml.YAMLError:
            kept = None
    if kept is None:
        return version.get("yaml") or ""
    return yaml.safe_dump(kept, sort_keys=False, allow_unicode=True)


def _short(value: Any) -> str:
    text = json.dumps(value, ensure_ascii=False, default=str)
    if len(text) <= _MAX_VALUE_CHARS:
        return text
    return f"{text[: _MAX_VALUE_CHARS - 1]}…"


def _render_changes(changes: list[VersionChange]) -> str:
    """The computed change list, one line per behavioural change."""
    lines: list[str] = []
    for change in changes:
        kind = change.get("kind")
        section = change.get("section", "")
        if kind == "field_changed":
            field = change.get("field", "")
            if field not in _NOT_BEHAVIOUR:
                lines.append(
                    f"- {field}: {_short(change.get('before'))} -> {_short(change.get('after'))}"
                )
        elif kind == "reordered":
            lines.append(f"- {section}: same steps, new order")
        elif kind in ("item_added", "item_removed"):
            added = kind == "item_added"
            item = change.get("after" if added else "before")
            verb = "added" if added else "removed"
            lines.append(f"- {section}: {verb} {_short(item) if item is not None else ''}".rstrip())
        elif kind == "item_changed":
            where = f"{section} #{change.get('index', 0) + 1}"
            details = change.get("details") or []
            if details and change.get("detail_count", len(details)) <= len(details):
                for detail in details:
                    path = ".".join(str(p) for p in detail.get("path", []))
                    lines.append(
                        f"- {where} {path}: {_short(detail.get('before'))}"
                        f" -> {_short(detail.get('after'))}"
                    )
            else:
                lines.append(
                    f"- {where}: {_short(change.get('before'))} -> {_short(change.get('after'))}"
                )
    return "\n".join(lines)


def _entity_names(hass: HomeAssistant, *texts: str) -> dict[str, str]:
    names: dict[str, str] = {}
    for text in texts:
        for entity_id in _ENTITY_ID_RE.findall(text):
            state = hass.states.get(entity_id)
            name = state.attributes.get("friendly_name") if state else None
            if isinstance(name, str) and name:
                names[entity_id] = name
    return names


async def _generate(
    hass: HomeAssistant, store: AutomationStore, automation_id: str, version_id: str
) -> None:
    versions = await store.get_versions(automation_id)
    index = next((i for i, v in enumerate(versions) if v["version_id"] == version_id), None)
    if not index:  # unknown, or the oldest kept version with nothing before it
        return
    version = versions[index]
    if not _needs_summary(version):
        return
    changes = _render_changes(version.get("changes") or [])
    if not changes:
        # Only the enabled state, id or description moved — nothing about how
        # the automation behaves, so there is no sentence to write.
        return
    llm = _get_llm(hass)
    if llm is None:
        return
    before, after = _document(versions[index - 1]), _document(version)
    if len(before) > _MAX_YAML_CHARS or len(after) > _MAX_YAML_CHARS:
        return
    language = summary_language(hass)
    summary = await llm.summarize_automation_change(
        before,
        after,
        _entity_names(hass, before, after, changes),
        language,
        changes=changes,
    )
    if summary:
        await store.async_set_version_summary(automation_id, version_id, summary, language)


async def _run(
    hass: HomeAssistant, store: AutomationStore, automation_id: str, version_id: str
) -> None:
    inflight: set[str] = hass.data.setdefault(DOMAIN, {}).setdefault(_INFLIGHT_KEY, set())
    try:
        await _generate(hass, store, automation_id, version_id)
    except (HomeAssistantError, OSError, TimeoutError, ValueError) as exc:
        _LOGGER.debug("Version summary for %s failed: %s", version_id, exc)
    finally:
        inflight.discard(version_id)


def schedule_version_summary(
    hass: HomeAssistant, store: AutomationStore, automation_id: str, version_id: str
) -> None:
    """Write one version's summary in the background, once."""
    inflight: set[str] = hass.data.setdefault(DOMAIN, {}).setdefault(_INFLIGHT_KEY, set())
    if version_id in inflight:
        return
    inflight.add(version_id)
    # A background task, so nothing that waits for HA to go quiet — a reload,
    # a test's async_block_till_done — sits out an LLM round trip.
    hass.async_create_background_task(
        _run(hass, store, automation_id, version_id),
        name=f"selora_ai_version_summary_{version_id}",
    )


def schedule_missing_summaries(
    hass: HomeAssistant,
    store: AutomationStore,
    automation_id: str,
    versions: list[AutomationVersion],
) -> None:
    """Queue summaries for the newest versions saved before they existed."""
    pending = [v for v in reversed(versions[1:]) if _needs_summary(v)]
    for version in pending[:BACKFILL_LIMIT]:
        schedule_version_summary(hass, store, automation_id, version["version_id"])
