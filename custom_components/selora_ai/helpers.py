"""Shared utility functions used across the Selora AI integration.

Consolidates duplicated helpers that previously existed in multiple modules
(__init__, mcp_server, llm_client, collector, automation_utils).
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
import json
import logging
from typing import TYPE_CHECKING, Any, Final

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from .automation_store import AutomationStore
    from .scene_store import SceneStore

from .const import AUTOMATION_ID_PREFIX, DOMAIN

_LOGGER = logging.getLogger(__name__)

# Serialises every in-integration writer of a Lovelace document.
#
# Saving a dashboard is read-modify-write of the WHOLE config, so two writers
# that load the same document both save it and the later one silently discards
# the other's work. It has to be shared rather than per-module: the recipe
# install/uninstall stage (`recipes.dashboard`) and the chat card/view editors
# (`dashboard_manager`) write the same documents, and a recipe install landing
# mid-edit is exactly the overlap a per-module lock leaves open.
#
# One lock for all dashboards rather than one per dashboard. These writes are
# rare and sub-millisecond, so the contention is theoretical, while a per-target
# registry has to key on something — and the default dashboard answers to None,
# "", and "lovelace" at different call sites, so the key is the part that would
# get it wrong.
#
# It does NOT cover the Lovelace UI, which saves the same document from the
# frontend. That is what the content fingerprints are for.
DASHBOARD_LOCK: Final = asyncio.Lock()


# Whether the caller of the tool call in flight is an admin.
#
# A ContextVar rather than a parameter because both tool surfaces dispatch
# through a handler signature that carries no identity — chat handlers take
# ``(arguments)`` and MCP handlers ``(hass, arguments)`` — and the alternative is
# threading a flag through every handler and manager function to serve the few
# that need it. Same shape as the per-call repair buffer in ``llm_client.usage``.
#
# Defaults to False so an unscoped call is treated as NON-admin: a caller that
# forgot to open the scope gets less access, never more.
CALLER_IS_ADMIN: ContextVar[bool] = ContextVar("selora_caller_is_admin", default=False)

# Whether the caller may perform WRITES. Separate from the above because the two
# answers genuinely differ: `_check_tool_access` lets a custom MCP token with an
# explicit tool allowlist, or a Selora JWT carrying the write scope, call the
# mutation tools without being an HA admin. Sharing one boolean told those
# callers a dashboard was read-only while their writes succeeded.
#
# Admin identity is the right question for a dashboard's own `require_admin`
# flag — that is about who Home Assistant hides the page from, not about scopes.
CALLER_CAN_WRITE: ContextVar[bool] = ContextVar("selora_caller_can_write", default=False)


@contextmanager
def caller_scope(is_admin: bool, *, can_write: bool | None = None) -> Iterator[None]:
    """Mark who the tool call in flight is running as.

    ``can_write`` defaults to ``is_admin`` — true on the chat surface, where the
    websocket handlers are admin-gated and the two are the same question.
    """
    admin_token = CALLER_IS_ADMIN.set(bool(is_admin))
    write_token = CALLER_CAN_WRITE.set(bool(is_admin if can_write is None else can_write))
    try:
        yield
    finally:
        CALLER_CAN_WRITE.reset(write_token)
        CALLER_IS_ADMIN.reset(admin_token)


async def is_auto_generated_dashboard(config: Any) -> bool:
    """Whether HA is generating this dashboard rather than serving a stored one.

    ``LovelaceStorage.async_load`` raises ``ConfigNotFound`` when its stored
    config is None, which is NOT an empty dashboard: the frontend renders the
    original-states strategy and the user sees a full Overview. Any writer that
    reads that as "nothing there yet" and saves a document of its own replaces
    everything the user could see.

    Shared because both write paths reach it independently — the chat/MCP tools
    through ``dashboard_manager``, and the recipe install stage through
    ``recipes.dashboard.async_place_card``, which seeds its own ``Home`` document
    on ``ConfigNotFound``.

    Fails CLOSED. This is the only thing standing between a transient storage
    error and a document saved over the Overview the user can see, so an
    indeterminate answer has to refuse the write rather than assume there is
    nothing to lose. The callers are already in a ``ConfigNotFound`` branch when
    they ask, so the same read has just failed once — an answer of "storage
    mode, go ahead" is the less likely reading of a second failure.
    """
    # `{}` from a failed probe has no "mode", so the default makes it auto-gen.
    return (await dashboard_info(config)).get("mode", "auto-gen") == "auto-gen"


async def dashboard_info(config: Any) -> dict[str, Any]:
    """``async_get_info()``, or ``{}`` when it fails.

    Callers must fail CLOSED on ``{}``: a dashboard whose metadata cannot be read
    is not evidence that writing to it is safe.
    """
    try:
        info = await config.async_get_info()
    except Exception:  # noqa: BLE001 — a probe must never break the caller
        return {}
    return info if isinstance(info, dict) else {}


def is_strategy_document(document: Any) -> bool:
    """Whether this document is built from a strategy rather than stored views.

    The built-in Map dashboard stores ``{"strategy": {...}}`` and no views. Its
    mode is ``storage`` and ``async_get_info`` reports ``storage`` too, so
    nothing short of the document itself distinguishes it — and a ``views`` list
    saved alongside is kept by the store and ignored by the frontend.
    """
    return isinstance(document, dict) and isinstance(document.get("strategy"), dict)


def default_dashboard_key(dashboards: Mapping[str | None, Any]) -> str | None:
    """The key of the dashboard an unqualified request means.

    Home Assistant is migrating the default Overview off the ``None`` key and
    onto a real dashboard entry keyed ``"lovelace"``: `_async_migrate_default_config`
    moves the stored config there and points the default panel at it, and a
    YAML-mode install registers its `LovelaceYAML` under the same key. In both
    cases `dashboards[None]` survives as an EMPTY `LovelaceStorage` placeholder
    that HA never registers a panel for.

    So `None` is not "the default" — it is the default only until `"lovelace"`
    exists. Treating them as interchangeable sends every unqualified read and
    write to a dashboard the user cannot see, which reads as data loss on a
    write and as an empty home on a read.
    """
    return "lovelace" if "lovelace" in dashboards else None


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


def sanitize_household_profile(value: object, limit: int) -> str:
    """Normalize and truncate the user-authored household profile.

    Unlike :func:`sanitize_untrusted_text`, this preserves newlines so a
    bullet-style profile survives, while still stripping other control
    characters (which could smuggle formatting/escapes into the prompt),
    collapsing trailing whitespace and runs of blank lines, and hard-capping
    the total length. The result still flows into the LLM as clearly-labeled
    informational context — never as instructions.
    """
    text = str(value or "")
    # Normalize newlines, then drop every control char except newline.
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [
        "".join(ch for ch in line if ch == "\t" or ch >= " ").rstrip() for line in text.split("\n")
    ]
    # Collapse 3+ consecutive blank lines down to a single blank line.
    cleaned: list[str] = []
    blank_run = 0
    for line in lines:
        if line:
            blank_run = 0
            cleaned.append(line)
        else:
            blank_run += 1
            if blank_run == 1:
                cleaned.append(line)
    result = "\n".join(cleaned).strip()
    if len(result) > limit:
        result = result[: limit - 3].rstrip() + "..."
    return result


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
    """Return True if this automation was created by Selora AI.

    Checks (in order):
      1. The `selora_ai` label is attached (new path — every Selora
         creation gets stamped with this label).
      2. The id starts with our reserved prefix (covers automations
         created via Selora's WS endpoints, label or not).
      3. The legacy ``[Selora AI]`` text marker is present in the
         alias or description (covers automations created before we
         switched to labels — keep recognising them so the
         Automations tab continues to filter them correctly).
    """
    from .const import SELORA_AI_LABEL_ID

    labels = automation.get("labels") or []
    if isinstance(labels, list) and SELORA_AI_LABEL_ID in labels:
        return True
    aid = str(automation.get("id", ""))
    if aid.startswith(AUTOMATION_ID_PREFIX):
        return True
    desc = str(automation.get("description", ""))
    alias = str(automation.get("alias", ""))
    return "[Selora AI]" in desc or alias.startswith("[Selora AI]")


# ── Integration-error rendering ────────────────────────────────────────────


def _integration_error_specifics(evidence: dict[str, Any]) -> str:
    """One-line human summary of an integration-error signal's evidence.

    Prefers the config-entry failure reason, then the repair issue's rendered
    title/description (populated by ``health_monitor``'s translation resolver).
    Returns "" when the evidence carries no legible detail.
    """
    reason = evidence.get("reason")
    if isinstance(reason, str) and reason.strip():
        text = reason.strip()
        return text if text.endswith((".", "!", "?")) else f"{text}."

    parts = [evidence.get("issue_title"), evidence.get("issue_description")]
    text = " — ".join(p.strip() for p in parts if isinstance(p, str) and p.strip())
    if text:
        return text if text.endswith((".", "!", "?")) else f"{text}."
    return ""


def integration_error_detail(target: str, evidence: dict[str, Any]) -> str:
    """User-facing detail for an integration-error signal.

    Shared by the audit checks (``insights_checks``) and the primary insight
    renderer (``insights``) so the panel and the atomic export say the same
    thing — leading with the concrete failure when one was captured, and
    falling back to a generic line otherwise.
    """
    specifics = _integration_error_specifics(evidence)
    if specifics:
        return (
            f"The {target} integration reported an error: {specifics} "
            "Check its configuration or credentials in Settings → Devices & Services."
        )
    return (
        f"The {target} integration reported an error — check its configuration "
        "or credentials in Settings → Devices & Services."
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
