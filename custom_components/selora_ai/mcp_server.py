"""Selora AI MCP Server — exposes the Selora intelligence layer over MCP.

Endpoint: POST /api/selora_ai/mcp
Protocol: Model Context Protocol 1.26.0, Streamable HTTP (stateless)
Auth:     Bearer token via HomeAssistantView.requires_auth = True

Phase 1 tools
─────────────
  selora_list_automations     List Selora-managed automations with status + risk
  selora_get_automation       Full automation detail with YAML and version history
  selora_validate_automation  Validate + risk-assess YAML without writing anything
  selora_create_automation    Create automation from externally-generated YAML
  selora_accept_automation    Enable/commit a pending automation
  selora_delete_automation    Delete a Selora-managed automation
  selora_get_home_snapshot    Current entity states grouped by area
  selora_chat                 Natural-language chat with Selora's LLM
  selora_list_sessions        Recent conversation sessions

Security
────────
  - All requests require HA Bearer token (HomeAssistantView.requires_auth)
  - Write tools enforce admin-level authorization
  - All user-controlled string fields pass through _sanitize_untrusted_text()
    before inclusion in responses (prompt-injection boundary)
  - selora_create_automation runs server-side validation + risk assessment
    regardless of the YAML's origin
  - Automations are created disabled (initial_state: false) by default

See docs/selora-mcp-server.md and docs/adr/ADR-001-selora-mcp-server.md.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from datetime import UTC, datetime
import hashlib
from http import HTTPStatus
import json
import logging
from pathlib import Path
from typing import Any

from aiohttp import web
import anyio
from homeassistant.components.http import KEY_HASS, HomeAssistantView
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import Unauthorized
from mcp import types
from mcp.server import Server
from mcp.shared.message import SessionMessage
from mcp.types import JSONRPCMessage

from .const import AUTOMATION_ID_PREFIX, DOMAIN, LIGHT_ENTITY_EXCLUDE_PATTERNS

_LOGGER = logging.getLogger(__name__)

_MCP_URL = "/api/selora_ai/mcp"
_TIMEOUT_SECS = 60
_CONTENT_TYPE_JSON = "application/json"

# ── Tool name constants ────────────────────────────────────────────────────────

TOOL_LIST_AUTOMATIONS = "selora_list_automations"
TOOL_GET_AUTOMATION = "selora_get_automation"
TOOL_VALIDATE_AUTOMATION = "selora_validate_automation"
TOOL_CREATE_AUTOMATION = "selora_create_automation"
TOOL_ACCEPT_AUTOMATION = "selora_accept_automation"
TOOL_DELETE_AUTOMATION = "selora_delete_automation"
TOOL_GET_HOME_SNAPSHOT = "selora_get_home_snapshot"
TOOL_CHAT = "selora_chat"
TOOL_LIST_SESSIONS = "selora_list_sessions"
TOOL_LIST_PATTERNS = "selora_list_patterns"
TOOL_GET_PATTERN = "selora_get_pattern"
TOOL_LIST_SUGGESTIONS = "selora_list_suggestions"
TOOL_ACCEPT_SUGGESTION = "selora_accept_suggestion"
TOOL_DISMISS_SUGGESTION = "selora_dismiss_suggestion"
TOOL_TRIGGER_SCAN = "selora_trigger_scan"


# ── Registration ───────────────────────────────────────────────────────────────


def register_mcp_server(hass: HomeAssistant) -> None:
    """Register the Selora AI MCP HTTP view with HA's HTTP server.

    Stores and the LLM client are retrieved lazily from hass.data at
    request time so this can be called during async_setup_entry before
    all stores are fully initialised.
    """
    hass.http.register_view(SeloraAIMCPView())
    _LOGGER.info("Selora AI MCP server registered at %s", _MCP_URL)


# ── HTTP view ──────────────────────────────────────────────────────────────────


class SeloraAIMCPView(HomeAssistantView):
    """Selora AI MCP endpoint — Streamable HTTP, stateless mode."""

    name = "selora_ai:mcp"
    url = _MCP_URL
    requires_auth = True  # Enforces Bearer token via HA auth subsystem

    async def post(self, request: web.Request) -> web.Response:
        """Handle a single MCP JSON-RPC request."""
        hass: HomeAssistant = request.app[KEY_HASS]

        # Content-type negotiation
        if _CONTENT_TYPE_JSON not in request.headers.get("accept", ""):
            return web.Response(
                status=HTTPStatus.BAD_REQUEST,
                text=f"Client must accept {_CONTENT_TYPE_JSON}",
            )
        if request.content_type != _CONTENT_TYPE_JSON:
            return web.Response(
                status=HTTPStatus.BAD_REQUEST,
                text=f"Content-Type must be {_CONTENT_TYPE_JSON}",
            )

        # Parse JSON-RPC message
        try:
            json_data = await request.json()
            message = JSONRPCMessage.model_validate(json_data)
        except Exception as err:
            _LOGGER.debug("Failed to parse MCP message: %s", err)
            return web.Response(
                status=HTTPStatus.BAD_REQUEST,
                text="Request must be a valid JSON-RPC message",
            )

        # For notifications/responses (no id field), return 202 Accepted
        if not hasattr(message.root, "id") or message.root.id is None:
            _LOGGER.debug("MCP notification received, returning 202")
            return web.Response(status=HTTPStatus.ACCEPTED)

        # Build a stateless server for this request
        user = request.get(KEY_HASS_USER, None)
        is_admin = bool(user and getattr(user, "is_admin", False))
        server, init_options = _create_selora_mcp_server(hass, is_admin=is_admin)

        # Stream pair for stateless request-response cycle
        read_writer, read_stream = anyio.create_memory_object_stream(0)
        write_stream, write_reader = anyio.create_memory_object_stream(0)

        async def _run() -> None:
            await server.run(read_stream, write_stream, init_options, stateless=True)

        try:
            async with asyncio.timeout(_TIMEOUT_SECS), anyio.create_task_group() as tg:
                tg.start_soon(_run)
                await read_writer.send(SessionMessage(message))
                response_msg = await anext(aiter(write_reader))
                tg.cancel_scope.cancel()
        except TimeoutError:
            _LOGGER.warning("MCP request timed out after %ss", _TIMEOUT_SECS)
            return web.Response(
                status=HTTPStatus.GATEWAY_TIMEOUT,
                text="MCP request timed out",
            )
        except Exception:
            _LOGGER.exception("MCP request failed")
            return web.Response(
                status=HTTPStatus.INTERNAL_SERVER_ERROR,
                text="Internal server error",
            )

        return web.json_response(
            data=response_msg.message.model_dump(by_alias=True, exclude_none=True)
        )


# Need KEY_HASS_USER for admin check — import safely
try:
    from homeassistant.components.http import KEY_HASS_USER
except ImportError:
    KEY_HASS_USER = "hass_user"  # type: ignore[assignment]


# ── MCP server factory ────────────────────────────────────────────────────────


def _create_selora_mcp_server(
    hass: HomeAssistant,
    is_admin: bool = False,
) -> tuple[Server, Any]:
    """Instantiate and configure a Selora MCP server for one stateless request."""

    server: Server = Server("selora-ai")

    @server.list_tools()  # type: ignore[no-untyped-call]
    async def _list_tools() -> list[types.Tool]:
        return _TOOL_DEFINITIONS

    @server.call_tool()  # type: ignore[no-untyped-call]
    async def _call_tool(name: str, arguments: dict[str, Any]) -> Sequence[types.TextContent]:
        return await _dispatch(hass, name, arguments, is_admin=is_admin)

    return server, server.create_initialization_options()


# ── Tool dispatch ──────────────────────────────────────────────────────────────


async def _dispatch(
    hass: HomeAssistant,
    name: str,
    arguments: dict[str, Any],
    *,
    is_admin: bool,
) -> list[types.TextContent]:
    """Route a tool call to its handler and return MCP TextContent."""
    try:
        if name == TOOL_LIST_AUTOMATIONS:
            result = await _tool_list_automations(hass, arguments)
        elif name == TOOL_GET_AUTOMATION:
            result = await _tool_get_automation(hass, arguments)
        elif name == TOOL_VALIDATE_AUTOMATION:
            result = await _tool_validate_automation(hass, arguments)
        elif name == TOOL_CREATE_AUTOMATION:
            _require_admin(is_admin)
            result = await _tool_create_automation(hass, arguments)
        elif name == TOOL_ACCEPT_AUTOMATION:
            _require_admin(is_admin)
            result = await _tool_accept_automation(hass, arguments)
        elif name == TOOL_DELETE_AUTOMATION:
            _require_admin(is_admin)
            result = await _tool_delete_automation(hass, arguments)
        elif name == TOOL_GET_HOME_SNAPSHOT:
            result = await _tool_get_home_snapshot(hass)
        elif name == TOOL_CHAT:
            _require_admin(is_admin)
            result = await _tool_chat(hass, arguments)
        elif name == TOOL_LIST_SESSIONS:
            result = await _tool_list_sessions(hass)
        elif name == TOOL_LIST_PATTERNS:
            result = await _tool_list_patterns(hass, arguments)
        elif name == TOOL_GET_PATTERN:
            result = await _tool_get_pattern(hass, arguments)
        elif name == TOOL_LIST_SUGGESTIONS:
            result = await _tool_list_suggestions(hass, arguments)
        elif name == TOOL_ACCEPT_SUGGESTION:
            _require_admin(is_admin)
            result = await _tool_accept_suggestion(hass, arguments)
        elif name == TOOL_DISMISS_SUGGESTION:
            _require_admin(is_admin)
            result = await _tool_dismiss_suggestion(hass, arguments)
        elif name == TOOL_TRIGGER_SCAN:
            _require_admin(is_admin)
            result = await _tool_trigger_scan(hass)
        else:
            result = {"error": f"Unknown tool: {name}"}
    except Unauthorized as exc:
        result = {"error": str(exc)}
    except Exception:
        _LOGGER.exception("Tool %s raised an exception", name)
        result = {"error": "Tool execution failed"}

    return [types.TextContent(type="text", text=json.dumps(result, ensure_ascii=False))]


def _require_admin(is_admin: bool) -> None:
    """Raise Unauthorized if the caller is not an HA admin."""
    if not is_admin:
        raise Unauthorized("Admin access is required for this Selora MCP tool")


# ── Helpers ────────────────────────────────────────────────────────────────────


def _sanitize(value: Any, limit: int = 200) -> str:
    """Normalize and truncate untrusted string fields before including in responses.

    Prevents prompt-injection via entity friendly names, automation aliases,
    or other user-controlled strings that flow into MCP tool responses and
    may subsequently be used as LLM context by the calling agent.
    """
    text = " ".join(str(value or "").split())
    if len(text) > limit:
        text = text[: limit - 3] + "..."
    return text


def _get_automation_store(hass: HomeAssistant):
    """Return (or lazily create) the AutomationStore singleton."""
    from .automation_store import AutomationStore

    domain_data = hass.data.setdefault(DOMAIN, {})
    if "_automation_store" not in domain_data:
        domain_data["_automation_store"] = AutomationStore(hass)
    return domain_data["_automation_store"]


def _get_conv_store(hass: HomeAssistant):
    """Return (or lazily create) the ConversationStore singleton."""
    from . import ConversationStore

    domain_data = hass.data.setdefault(DOMAIN, {})
    return domain_data.setdefault("_conv_store", ConversationStore(hass))


def _get_llm(hass: HomeAssistant):
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
    from .automation_utils import _read_automations_yaml

    path = Path(hass.config.config_dir) / "automations.yaml"
    return await hass.async_add_executor_job(_read_automations_yaml, path)


def _is_selora(automation: dict[str, Any]) -> bool:
    """Return True if this automation was created by Selora AI."""
    aid = str(automation.get("id", ""))
    desc = str(automation.get("description", ""))
    alias = str(automation.get("alias", ""))
    return (
        aid.startswith(AUTOMATION_ID_PREFIX)
        or "[Selora AI]" in desc
        or alias.startswith("[Selora AI]")
    )


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


# ── Tool: selora_list_automations ─────────────────────────────────────────────


async def _tool_list_automations(
    hass: HomeAssistant, arguments: dict[str, Any]
) -> list[dict[str, Any]]:
    """Return Selora-managed automations with status and risk metadata."""
    from .automation_utils import assess_automation_risk

    status_filter: str | None = arguments.get("status")
    yaml_automations = await _read_yaml_automations(hass)
    store = _get_automation_store(hass)

    # Build a live state lookup (enabled/disabled) from HA state machine
    live_states: dict[str, str] = {}
    for state in hass.states.async_all("automation"):
        friendly = state.attributes.get("friendly_name", "")
        # Map by alias — will correlate below
        live_states[friendly] = state.state  # "on" or "off"

    result: list[dict[str, Any]] = []
    for auto in yaml_automations:
        if not _is_selora(auto):
            continue

        automation_id = str(auto.get("id", ""))
        alias = _sanitize(auto.get("alias", ""))

        meta = await store.get_metadata(automation_id)
        record = await store.get_record(automation_id)

        # Determine display status
        if _is_pending_automation(auto, record):
            status = "pending"
        else:
            # Cross-reference live HA state
            live = live_states.get(alias, "")
            status = "enabled" if live == "on" else "disabled"

        if status_filter and status != status_filter:
            continue

        risk = assess_automation_risk(auto)

        result.append(
            {
                "automation_id": automation_id,
                "alias": alias,
                "status": status,
                "version_count": meta["version_count"] if meta else 1,
                "current_version_id": meta["current_version_id"] if meta else None,
                "risk_assessment": _sanitize_risk(risk),
            }
        )

    return result


# ── Tool: selora_get_automation ───────────────────────────────────────────────


async def _tool_get_automation(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Return full detail for a single automation including YAML and version history."""
    import yaml as _yaml

    from .automation_utils import assess_automation_risk

    automation_id = str(arguments.get("automation_id", ""))
    if not automation_id:
        return {"error": "automation_id is required"}

    yaml_automations = await _read_yaml_automations(hass)
    auto = next((a for a in yaml_automations if str(a.get("id")) == automation_id), None)
    if auto is None:
        return {"error": f"Automation {automation_id} not found"}

    store = _get_automation_store(hass)
    record = await store.get_record(automation_id)
    versions: list[dict[str, Any]] = []
    lineage: list[dict[str, Any]] = []
    if record:
        for v in record.get("versions", []):
            versions.append(
                {
                    "version_id": v["version_id"],
                    "created_at": v.get("created_at", ""),
                    "message": _sanitize(v.get("message", "")),
                    "session_id": v.get("session_id"),
                }
            )
        lineage = [
            {
                "version_id": le.get("version_id"),
                "session_id": le.get("session_id"),
                "action": le.get("action"),
                "timestamp": le.get("timestamp"),
            }
            for le in record.get("lineage", [])
        ]

    yaml_text = _yaml.dump(auto, allow_unicode=True, default_flow_style=False)
    risk = assess_automation_risk(auto)

    return {
        "automation_id": automation_id,
        "alias": _sanitize(auto.get("alias", "")),
        "yaml": yaml_text,
        "status": "pending"
        if _is_pending_automation(auto, record)
        else ("disabled" if not auto.get("initial_state", True) else "enabled"),
        "version_count": len(versions),
        "versions": versions,
        "lineage": lineage,
        "risk_assessment": _sanitize_risk(risk),
    }


# ── Tool: selora_validate_automation ──────────────────────────────────────────


async def _tool_validate_automation(
    hass: HomeAssistant, arguments: dict[str, Any]
) -> dict[str, Any]:
    """Validate + risk-assess YAML without creating anything. Pure read."""
    import yaml as _yaml

    from .automation_utils import assess_automation_risk, validate_automation_payload

    yaml_text = str(arguments.get("yaml", ""))
    if not yaml_text.strip():
        return {
            "valid": False,
            "errors": ["yaml field is required"],
            "normalized_yaml": None,
            "risk_assessment": None,
        }

    # Parse
    try:
        parsed = await hass.async_add_executor_job(lambda: _yaml.safe_load(yaml_text))
    except _yaml.YAMLError as exc:
        return {
            "valid": False,
            "errors": [f"YAML parse error: {exc}"],
            "normalized_yaml": None,
            "risk_assessment": None,
        }

    if not isinstance(parsed, dict):
        return {
            "valid": False,
            "errors": ["YAML must be a mapping"],
            "normalized_yaml": None,
            "risk_assessment": None,
        }

    # Validate
    is_valid, reason, normalized = validate_automation_payload(parsed)
    if not is_valid or normalized is None:
        return {
            "valid": False,
            "errors": [reason] if reason else ["Validation failed"],
            "normalized_yaml": None,
            "risk_assessment": None,
        }

    normalized_yaml = _yaml.dump(normalized, allow_unicode=True, default_flow_style=False)
    risk = assess_automation_risk(normalized)

    return {
        "valid": True,
        "errors": [],
        "normalized_yaml": normalized_yaml,
        "risk_assessment": _sanitize_risk(risk),
    }


# ── Tool: selora_create_automation ────────────────────────────────────────────


async def _tool_create_automation(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Create an automation from externally-provided YAML.

    Server-side validation and risk assessment run unconditionally.
    Automations are created disabled by default.
    """
    import yaml as _yaml

    from .automation_utils import (
        assess_automation_risk,
        async_create_automation,
        validate_automation_payload,
    )

    yaml_text = str(arguments.get("yaml", ""))
    enabled: bool = bool(arguments.get("enabled", False))
    version_message: str = _sanitize(arguments.get("version_message", "Created via MCP"))

    if not yaml_text.strip():
        return {"error": "yaml field is required"}

    try:
        parsed = await hass.async_add_executor_job(lambda: _yaml.safe_load(yaml_text))
    except _yaml.YAMLError as exc:
        return {"error": f"YAML parse error: {exc}"}

    if not isinstance(parsed, dict):
        return {"error": "YAML must be a mapping"}

    is_valid, reason, normalized = validate_automation_payload(parsed)
    if not is_valid or normalized is None:
        return {"error": f"Invalid automation: {reason}"}

    risk = assess_automation_risk(normalized)

    # Enforce disabled-by-default
    normalized["initial_state"] = enabled

    success = await async_create_automation(hass, normalized, version_message=version_message)
    if not success:
        return {"error": "Failed to write automation to automations.yaml"}

    # Retrieve the automation_id that was assigned during creation
    yaml_automations = await _read_yaml_automations(hass)
    alias = normalized.get("alias", "")
    created = next(
        (a for a in yaml_automations if a.get("alias") == alias and _is_selora(a)),
        None,
    )
    automation_id = str(created.get("id", "")) if created else ""

    return {
        "automation_id": automation_id,
        "status": "created",
        "risk_assessment": _sanitize_risk(risk),
    }


# ── Tool: selora_accept_automation ────────────────────────────────────────────


async def _tool_accept_automation(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Enable/commit a pending Selora automation."""
    from .automation_utils import async_update_automation

    automation_id = str(arguments.get("automation_id", ""))
    enabled: bool = bool(arguments.get("enabled", False))

    if not automation_id:
        return {"error": "automation_id is required"}

    yaml_automations = await _read_yaml_automations(hass)
    auto = next((a for a in yaml_automations if str(a.get("id")) == automation_id), None)
    if auto is None or not _is_selora(auto):
        return {"error": f"Selora automation {automation_id} not found"}

    updated = dict(auto)
    updated["initial_state"] = enabled

    success = await async_update_automation(
        hass,
        automation_id,
        updated,
        version_message="Accepted via MCP",
    )
    if not success:
        return {"error": "Failed to update automation"}

    return {"automation_id": automation_id, "status": "enabled" if enabled else "disabled"}


# ── Tool: selora_delete_automation ────────────────────────────────────────────


async def _tool_delete_automation(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Delete a Selora-managed automation."""
    from .automation_utils import async_delete_automation

    automation_id = str(arguments.get("automation_id", ""))
    if not automation_id:
        return {"error": "automation_id is required"}

    yaml_automations = await _read_yaml_automations(hass)
    auto = next((a for a in yaml_automations if str(a.get("id")) == automation_id), None)
    if auto is None or not _is_selora(auto):
        return {"error": f"Selora automation {automation_id} not found"}

    success = await async_delete_automation(hass, automation_id)
    if not success:
        return {"error": "Failed to delete automation"}

    return {"automation_id": automation_id, "status": "deleted"}


# ── Tool: selora_get_home_snapshot ────────────────────────────────────────────


def _format_state_value(value: str) -> str:
    """Format entity state values for human-readable display.

    Converts ISO 8601 timestamps to 12-hour HH:MM AM/PM format.
    Other values are sanitized normally.
    """
    from datetime import datetime

    stripped = value.strip()
    # Try parsing ISO 8601 datetime (e.g., 2026-03-27T11:05:27+00:00)
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S"):
        try:
            dt = datetime.strptime(stripped, fmt)
            return dt.strftime("%I:%M %p").lstrip("0")
        except ValueError:
            continue
    return _sanitize(value, limit=64)


async def _tool_get_home_snapshot(hass: HomeAssistant) -> dict[str, Any]:
    """Return current entity states grouped by HA area."""
    from homeassistant.helpers import area_registry as ar
    from homeassistant.helpers import entity_registry as er

    area_reg = ar.async_get(hass)
    entity_reg = er.async_get(hass)

    # Build area_id → area_name map
    area_names: dict[str, str] = {
        area.id: _sanitize(area.name) for area in area_reg.async_list_areas()
    }

    areas: dict[str, list[dict[str, Any]]] = {name: [] for name in area_names.values()}
    unassigned: list[dict[str, Any]] = []

    # Skip domains that are noisy / not useful for home snapshot context
    _SKIP_DOMAINS = {
        "scene",
        "script",
        "group",
        "sun",
        "zone",
        "persistent_notification",
        "device_tracker",
    }

    for state in hass.states.async_all():
        domain = state.entity_id.split(".")[0]
        if domain in _SKIP_DOMAINS:
            continue
        if domain == "light" and any(
            pat in state.entity_id for pat in LIGHT_ENTITY_EXCLUDE_PATTERNS
        ):
            continue

        entry = entity_reg.async_get(state.entity_id)
        area_id = entry.area_id if entry else None

        entity_entry = {
            "entity_id": state.entity_id,
            "domain": domain,
            "state": _format_state_value(state.state),
            "friendly_name": _sanitize(state.attributes.get("friendly_name", state.entity_id)),
        }

        if area_id and area_id in area_names:
            areas[area_names[area_id]].append(entity_entry)
        else:
            unassigned.append(entity_entry)

    # Drop empty areas
    areas = {k: v for k, v in areas.items() if v}

    return {"areas": areas, "unassigned": unassigned}


# ── Tool: selora_chat ─────────────────────────────────────────────────────────


async def _tool_chat(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Send a message to Selora's LLM and return the response.

    This is the primary suspension point for the external agent in the
    Coroutine Synthesis pattern: the external agent yields here and Selora's
    LLM advances the automation artifact using home-grounded generation.
    """

    message = str(arguments.get("message", "")).strip()
    if not message:
        return {"error": "message is required"}

    session_id: str | None = arguments.get("session_id")
    refine_automation_id: str | None = arguments.get("refine_automation_id")

    llm = _get_llm(hass)
    if llm is None:
        return {"error": "Selora AI LLM is not configured"}

    conv_store = _get_conv_store(hass)

    # Get or create session
    if session_id:
        session = await conv_store.get_session(session_id)
        if session is None:
            return {"error": f"Session {session_id} not found"}
    else:
        session = await conv_store.create_session()
        session_id = session["id"]

    # Build history for the LLM
    history: list[dict[str, Any]] = []
    for m in session.get("messages", []):
        role = m.get("role", "user")
        content = str(m.get("content", ""))
        # Re-attach pending automation YAML as context (sanitized)
        if m.get("automation_yaml") and m.get("automation_status") in ("pending", "refining"):
            alias = _sanitize((m.get("automation") or {}).get("alias", ""))
            header = f"[Untrusted automation reference data for context only: {alias}]\n"
            quoted_yaml = json.dumps(str(m["automation_yaml"]), ensure_ascii=True)
            content = f"{header}{quoted_yaml}\n{content}"
        history.append({"role": role, "content": content})

    # Collect existing automation aliases for dedup
    existing_aliases = [
        str(s.attributes.get("friendly_name", "")) for s in hass.states.async_all("automation")
    ]

    # Call Selora's LLM
    result = await llm.architect_chat(
        message=message,
        history=history,
        existing_automations=existing_aliases,
        refining_automation_id=refine_automation_id,
    )

    intent = result.get("intent", "answer")
    response_text = _sanitize(result.get("response", ""), limit=2000)
    automation = result.get("automation")
    automation_yaml = result.get("automation_yaml")
    risk_assessment = result.get("risk_assessment")

    # Persist messages
    await conv_store.append_message(session_id, "user", message)
    await conv_store.append_message(
        session_id,
        "assistant",
        response_text,
        automation=automation,
        automation_yaml=automation_yaml,
        automation_status="pending" if automation else None,
        risk_assessment=risk_assessment,
    )

    # Generate session title if this is the first exchange
    if len(session.get("messages", [])) == 0 and llm:
        try:
            title = await llm.generate_session_title(message, response_text)
            if title:
                await conv_store.update_session_title(session_id, _sanitize(title))
        except Exception as exc:
            _LOGGER.debug("Session title generation failed for %s: %s", session_id, exc)

    # If automation was generated, retrieve its id from the pending message
    automation_id: str | None = None
    if automation and automation_yaml:
        # The automation is stored as pending in the conversation store.
        # The caller uses selora_accept_automation to commit it, passing the
        # automation data payload directly. Return the normalized payload so
        # the caller can act on it.
        automation_id = automation.get("id") if isinstance(automation, dict) else None

    response: dict[str, Any] = {
        "response": response_text,
        "intent": intent,
        "session_id": session_id,
    }
    if automation_yaml:
        response["automation_yaml"] = automation_yaml
    if automation_id:
        response["automation_id"] = automation_id
    if risk_assessment:
        response["risk_assessment"] = _sanitize_risk(risk_assessment)

    return response


# ── Tool: selora_list_sessions ────────────────────────────────────────────────


async def _tool_list_sessions(hass: HomeAssistant) -> list[dict[str, Any]]:
    """Return recent conversation sessions (title + id, no messages)."""
    conv_store = _get_conv_store(hass)
    sessions = await conv_store.list_sessions()
    return [
        {
            "session_id": s["id"],
            "title": _sanitize(s.get("title", "Untitled")),
            "updated_at": s.get("updated_at", ""),
            "message_count": s.get("message_count", 0),
        }
        for s in sessions
    ]


def _find_collector(hass: HomeAssistant):
    domain_data = hass.data.get(DOMAIN, {})
    for key, value in domain_data.items():
        if key.startswith("_"):
            continue
        if isinstance(value, dict) and "collector" in value:
            return value["collector"]
    return None


def _get_suggestion_status_store(hass: HomeAssistant) -> dict[str, dict[str, Any]]:
    domain_data = hass.data.setdefault(DOMAIN, {})
    return domain_data.setdefault("_mcp_suggestion_status", {})


def _collect_entity_ids(value: Any) -> list[str]:
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
    return sorted(found)


def _suggestion_identity(raw: dict[str, Any], index: int) -> tuple[str, str]:
    automation_yaml = str(raw.get("automation_yaml", ""))
    alias = str(raw.get("alias", ""))
    digest_source = automation_yaml or json.dumps(
        {
            "alias": alias,
            "trigger": raw.get("trigger"),
            "triggers": raw.get("triggers"),
            "action": raw.get("action"),
            "actions": raw.get("actions"),
            "index": index,
        },
        sort_keys=True,
        default=str,
    )
    digest = hashlib.sha256(digest_source.encode("utf-8")).hexdigest()[:16]
    return f"sugg_{digest}", f"pattern_{digest}"


def _normalize_suggestion(
    raw: dict[str, Any], *, index: int, status_store: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    suggestion_id, fallback_pattern_id = _suggestion_identity(raw, index)
    persisted = status_store.get(suggestion_id, {})
    status = persisted.get("status", "pending")
    created_at = (
        persisted.get("created_at") or raw.get("created_at") or datetime.now(UTC).isoformat()
    )
    automation_yaml = str(raw.get("automation_yaml", ""))
    description = _sanitize(raw.get("description", raw.get("alias", "")), limit=400)
    confidence_raw = raw.get("confidence", 0.7)
    try:
        confidence = max(0.0, min(1.0, float(confidence_raw)))
    except (TypeError, ValueError):
        confidence = 0.7

    entity_ids = _collect_entity_ids(raw.get("automation_data") or raw)
    evidence_summary = _sanitize(
        raw.get("evidence_summary") or raw.get("evidence") or description,
        limit=500,
    )

    risk_assessment = raw.get("risk_assessment")
    if isinstance(risk_assessment, dict):
        risk = _sanitize_risk(risk_assessment)
    else:
        risk = {
            "level": "normal",
            "flags": [],
            "reasons": [],
            "scrutiny_tags": [],
            "summary": "No risk assessment available.",
        }

    suggestion = {
        "suggestion_id": suggestion_id,
        "pattern_id": str(raw.get("pattern_id") or fallback_pattern_id),
        "description": description,
        "confidence": confidence,
        "automation_yaml": automation_yaml,
        "evidence_summary": evidence_summary,
        "risk_assessment": risk,
        "status": status,
        "created_at": created_at,
        "entity_ids": entity_ids,
    }

    if suggestion_id not in status_store:
        status_store[suggestion_id] = {
            "status": status,
            "created_at": created_at,
        }

    return suggestion


async def _phase2_suggestions(hass: HomeAssistant) -> list[dict[str, Any]]:
    raw_items = hass.data.get(DOMAIN, {}).get("latest_suggestions", [])
    status_store = _get_suggestion_status_store(hass)
    results: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_items):
        if not isinstance(raw, dict):
            continue
        results.append(_normalize_suggestion(raw, index=index, status_store=status_store))
    return results


async def _tool_list_suggestions(
    hass: HomeAssistant, arguments: dict[str, Any]
) -> list[dict[str, Any]]:
    status_filter = str(arguments.get("status", "")).strip()
    suggestions = await _phase2_suggestions(hass)
    if status_filter:
        suggestions = [s for s in suggestions if s.get("status") == status_filter]
    return [
        {
            "suggestion_id": s["suggestion_id"],
            "pattern_id": s["pattern_id"],
            "description": s["description"],
            "confidence": s["confidence"],
            "automation_yaml": s["automation_yaml"],
            "evidence_summary": s["evidence_summary"],
            "risk_assessment": s["risk_assessment"],
            "status": s["status"],
            "created_at": s["created_at"],
        }
        for s in suggestions
    ]


async def _tool_list_patterns(
    hass: HomeAssistant, arguments: dict[str, Any]
) -> list[dict[str, Any]]:
    type_filter = str(arguments.get("type", "")).strip()
    status_filter = str(arguments.get("status", "")).strip()
    min_confidence_raw = arguments.get("min_confidence")
    min_confidence: float | None = None
    if min_confidence_raw is not None:
        try:
            min_confidence = float(min_confidence_raw)
        except (TypeError, ValueError):
            min_confidence = None

    suggestions = await _phase2_suggestions(hass)
    patterns: dict[str, dict[str, Any]] = {}
    status_rank = {
        "pending": 4,
        "active": 4,
        "accepted": 3,
        "snoozed": 2,
        "dismissed": 1,
    }

    for suggestion in suggestions:
        pattern_id = suggestion["pattern_id"]
        pattern_type = "correlation"
        suggestion_status = str(suggestion.get("status", "pending"))
        if suggestion_status == "pending":
            suggestion_status = "active"
        if pattern_id not in patterns:
            patterns[pattern_id] = {
                "pattern_id": pattern_id,
                "type": pattern_type,
                "description": suggestion["description"],
                "confidence": suggestion["confidence"],
                "entity_ids": list(suggestion.get("entity_ids", [])),
                "evidence": {
                    "evidence_summary": suggestion["evidence_summary"],
                    "suggestion_ids": [suggestion["suggestion_id"]],
                },
                "status": suggestion_status,
                "detected_at": suggestion["created_at"],
                "last_seen": suggestion["created_at"],
                "occurrence_count": 1,
            }
            continue

        current = patterns[pattern_id]
        current["occurrence_count"] += 1
        current["confidence"] = max(float(current["confidence"]), float(suggestion["confidence"]))
        current["entity_ids"] = sorted(
            set(current["entity_ids"]) | set(suggestion.get("entity_ids", []))
        )
        current["last_seen"] = max(str(current["last_seen"]), str(suggestion["created_at"]))
        current["evidence"]["suggestion_ids"].append(suggestion["suggestion_id"])

        current_rank = status_rank.get(str(current["status"]), 0)
        candidate_rank = status_rank.get(suggestion_status, 0)
        if candidate_rank > current_rank:
            current["status"] = suggestion_status

    result = list(patterns.values())

    if type_filter:
        result = [p for p in result if p.get("type") == type_filter]
    if status_filter:
        result = [p for p in result if p.get("status") == status_filter]
    if min_confidence is not None:
        result = [p for p in result if float(p.get("confidence", 0.0)) >= min_confidence]

    return result


async def _tool_get_pattern(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    pattern_id = str(arguments.get("pattern_id", "")).strip()
    if not pattern_id:
        return {"error": "pattern_id is required"}

    patterns = await _tool_list_patterns(hass, {})
    pattern = next((p for p in patterns if p.get("pattern_id") == pattern_id), None)
    if pattern is None:
        return {"error": f"Pattern {pattern_id} not found"}

    suggestions = await _phase2_suggestions(hass)
    linked = [
        {
            "suggestion_id": s["suggestion_id"],
            "description": s["description"],
            "status": s["status"],
            "confidence": s["confidence"],
            "created_at": s["created_at"],
        }
        for s in suggestions
        if s.get("pattern_id") == pattern_id
    ]

    return {
        **pattern,
        "suggestions": linked,
    }


async def _tool_accept_suggestion(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    suggestion_id = str(arguments.get("suggestion_id", "")).strip()
    enabled = bool(arguments.get("enabled", False))
    if not suggestion_id:
        return {"error": "suggestion_id is required"}

    suggestions = await _phase2_suggestions(hass)
    target = next((s for s in suggestions if s.get("suggestion_id") == suggestion_id), None)
    if target is None:
        return {"error": f"Suggestion {suggestion_id} not found"}

    if not target.get("automation_yaml"):
        return {"error": "Suggestion does not include automation_yaml"}

    created = await _tool_create_automation(
        hass,
        {
            "yaml": target["automation_yaml"],
            "enabled": enabled,
            "version_message": f"Created from suggestion {suggestion_id}",
        },
    )
    if "error" in created:
        return created

    status_store = _get_suggestion_status_store(hass)
    status_store[suggestion_id] = {
        "status": "accepted",
        "created_at": status_store.get(suggestion_id, {}).get("created_at", target["created_at"]),
        "updated_at": datetime.now(UTC).isoformat(),
    }

    return {
        "suggestion_id": suggestion_id,
        "status": "accepted",
        "automation_id": created.get("automation_id", ""),
        "risk_assessment": target.get("risk_assessment"),
    }


async def _tool_dismiss_suggestion(
    hass: HomeAssistant, arguments: dict[str, Any]
) -> dict[str, Any]:
    suggestion_id = str(arguments.get("suggestion_id", "")).strip()
    reason = _sanitize(arguments.get("reason", ""), limit=300)
    if not suggestion_id:
        return {"error": "suggestion_id is required"}

    suggestions = await _phase2_suggestions(hass)
    target = next((s for s in suggestions if s.get("suggestion_id") == suggestion_id), None)
    if target is None:
        return {"error": f"Suggestion {suggestion_id} not found"}

    now_iso = datetime.now(UTC).isoformat()
    dismissal_reason = reason if reason else "user-declined"

    # Update in-memory status overlay (used by phase-2 suggestion rendering)
    status_store = _get_suggestion_status_store(hass)
    status_store[suggestion_id] = {
        "status": "dismissed",
        "reason": dismissal_reason,
        "created_at": status_store.get(suggestion_id, {}).get("created_at", target["created_at"]),
        "updated_at": now_iso,
    }

    # Persist to PatternStore so dismissal survives HA restarts (#43)
    pattern_store = hass.data.get(DOMAIN, {}).get("pattern_store")
    if pattern_store is not None:
        await pattern_store.update_suggestion_status(
            suggestion_id,
            status="dismissed",
            dismissed_at=now_iso,
            dismissal_reason=dismissal_reason,
        )
    else:
        _LOGGER.warning(
            "pattern_store not available — dismissal for %s not persisted to storage",
            suggestion_id,
        )

    return {
        "suggestion_id": suggestion_id,
        "status": "dismissed",
        "reason": dismissal_reason,
    }


async def _tool_trigger_scan(hass: HomeAssistant) -> dict[str, Any]:
    domain_data = hass.data.setdefault(DOMAIN, {})
    now = datetime.now(UTC)
    last_scan_iso = domain_data.get("_mcp_last_scan_at")

    if isinstance(last_scan_iso, str):
        try:
            last_scan = datetime.fromisoformat(last_scan_iso)
            delta = (now - last_scan).total_seconds()
            if delta < 60:
                suggestions = await _phase2_suggestions(hass)
                return {
                    "patterns_detected": len({s["pattern_id"] for s in suggestions}),
                    "suggestions_generated": len(suggestions),
                    "scan_duration_ms": 0,
                    "cached": True,
                }
        except ValueError:
            pass

    collector = _find_collector(hass)
    if collector is None:
        return {"error": "No collector available — check LLM configuration"}

    started = datetime.now(UTC)
    await collector._collect_analyze_log()
    finished = datetime.now(UTC)

    domain_data["_mcp_last_scan_at"] = finished.isoformat()
    suggestions = await _phase2_suggestions(hass)

    return {
        "patterns_detected": len({s["pattern_id"] for s in suggestions}),
        "suggestions_generated": len(suggestions),
        "scan_duration_ms": int((finished - started).total_seconds() * 1000),
        "cached": False,
    }


# ── Risk assessment sanitizer ─────────────────────────────────────────────────


def _sanitize_risk(risk: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of a risk_assessment dict with all strings sanitized."""
    return {
        "level": risk.get("level", "normal"),
        "flags": list(risk.get("flags", [])),
        "reasons": [_sanitize(r, limit=300) for r in risk.get("reasons", [])],
        "scrutiny_tags": list(risk.get("scrutiny_tags", [])),
        "summary": _sanitize(risk.get("summary", ""), limit=300),
    }


# ── Tool definitions (MCP schema) ─────────────────────────────────────────────


_TOOL_DEFINITIONS: list[types.Tool] = [
    types.Tool(
        name=TOOL_LIST_AUTOMATIONS,
        description=(
            "List Selora AI-managed automations with their status and risk assessment. "
            "Filter by status: pending, enabled, or disabled."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "enum": ["pending", "enabled", "disabled"],
                    "description": "Filter by automation status. Omit to return all.",
                }
            },
        },
    ),
    types.Tool(
        name=TOOL_GET_AUTOMATION,
        description=(
            "Return full detail for a single Selora automation: YAML, version history, "
            "lineage, and risk assessment."
        ),
        inputSchema={
            "type": "object",
            "required": ["automation_id"],
            "properties": {
                "automation_id": {"type": "string", "description": "The Selora automation ID."}
            },
        },
    ),
    types.Tool(
        name=TOOL_VALIDATE_AUTOMATION,
        description=(
            "Validate and risk-assess a YAML string representing a Home Assistant automation "
            "WITHOUT creating or modifying anything. Use this to check externally-generated "
            "YAML before committing. Returns validation errors and a risk assessment."
        ),
        inputSchema={
            "type": "object",
            "required": ["yaml"],
            "properties": {
                "yaml": {
                    "type": "string",
                    "description": "Raw YAML string for a Home Assistant automation.",
                }
            },
        },
    ),
    types.Tool(
        name=TOOL_CREATE_AUTOMATION,
        description=(
            "Create a new Home Assistant automation from a YAML string. "
            "Server-side validation and risk assessment run unconditionally. "
            "Automations are created DISABLED by default — set enabled=true to override. "
            "Requires admin access."
        ),
        inputSchema={
            "type": "object",
            "required": ["yaml"],
            "properties": {
                "yaml": {"type": "string", "description": "Raw YAML for the automation."},
                "enabled": {
                    "type": "boolean",
                    "default": False,
                    "description": "Whether to enable the automation immediately. Defaults to false.",
                },
                "version_message": {
                    "type": "string",
                    "description": "Optional note recorded in the version history.",
                },
            },
        },
    ),
    types.Tool(
        name=TOOL_ACCEPT_AUTOMATION,
        description=(
            "Enable or update a Selora automation that is currently disabled. "
            "Requires admin access."
        ),
        inputSchema={
            "type": "object",
            "required": ["automation_id"],
            "properties": {
                "automation_id": {"type": "string"},
                "enabled": {
                    "type": "boolean",
                    "default": False,
                    "description": "Set true to enable immediately, false to keep disabled.",
                },
            },
        },
    ),
    types.Tool(
        name=TOOL_DELETE_AUTOMATION,
        description=("Delete a Selora-managed automation permanently. Requires admin access."),
        inputSchema={
            "type": "object",
            "required": ["automation_id"],
            "properties": {"automation_id": {"type": "string"}},
        },
    ),
    types.Tool(
        name=TOOL_GET_HOME_SNAPSHOT,
        description=(
            "Return current Home Assistant entity states grouped by area. "
            "Call this first to understand what entities and areas exist before "
            "generating or requesting any automation."
        ),
        inputSchema={"type": "object", "properties": {}},
    ),
    types.Tool(
        name=TOOL_CHAT,
        description=(
            "Send a natural-language message to Selora's internal LLM in the context of "
            "the current home state. Returns a response and, where applicable, a proposed "
            "automation with YAML and risk assessment. "
            "Pass session_id to continue an existing conversation. "
            "Pass refine_automation_id to refine a specific pending automation. "
            "This is the primary Coroutine Synthesis suspension point: the external agent "
            "yields here and Selora advances the automation artifact using home-grounded "
            "generation. Requires admin access."
        ),
        inputSchema={
            "type": "object",
            "required": ["message"],
            "properties": {
                "message": {"type": "string"},
                "session_id": {
                    "type": "string",
                    "description": "Continue an existing session. Omit to start a new one.",
                },
                "refine_automation_id": {
                    "type": "string",
                    "description": "Refine a specific pending automation.",
                },
            },
        },
    ),
    types.Tool(
        name=TOOL_LIST_SESSIONS,
        description="Return recent Selora chat sessions (title, id, timestamp). No messages included.",
        inputSchema={"type": "object", "properties": {}},
    ),
    types.Tool(
        name=TOOL_LIST_PATTERNS,
        description=(
            "List detected behavior patterns derived from Selora suggestions. "
            "Supports filtering by type, confidence, and status."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "type": {
                    "type": "string",
                    "enum": ["time_based", "correlation", "sequence"],
                },
                "min_confidence": {
                    "type": "number",
                    "minimum": 0.0,
                    "maximum": 1.0,
                },
                "status": {
                    "type": "string",
                    "enum": ["active", "dismissed", "snoozed", "accepted"],
                },
            },
        },
    ),
    types.Tool(
        name=TOOL_GET_PATTERN,
        description="Return full detail for one pattern, including linked suggestions.",
        inputSchema={
            "type": "object",
            "required": ["pattern_id"],
            "properties": {"pattern_id": {"type": "string"}},
        },
    ),
    types.Tool(
        name=TOOL_LIST_SUGGESTIONS,
        description=(
            "List proactive automation suggestions with YAML previews and risk assessment. "
            "Supports status filtering."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "enum": ["pending", "accepted", "dismissed", "snoozed"],
                }
            },
        },
    ),
    types.Tool(
        name=TOOL_ACCEPT_SUGGESTION,
        description=(
            "Create an automation from a pending suggestion and mark it accepted. "
            "Requires admin access."
        ),
        inputSchema={
            "type": "object",
            "required": ["suggestion_id"],
            "properties": {
                "suggestion_id": {"type": "string"},
                "enabled": {
                    "type": "boolean",
                    "default": False,
                },
            },
        },
    ),
    types.Tool(
        name=TOOL_DISMISS_SUGGESTION,
        description=(
            "Mark a suggestion as dismissed. Optionally include a reason. Requires admin access."
        ),
        inputSchema={
            "type": "object",
            "required": ["suggestion_id"],
            "properties": {
                "suggestion_id": {"type": "string"},
                "reason": {"type": "string"},
            },
        },
    ),
    types.Tool(
        name=TOOL_TRIGGER_SCAN,
        description=(
            "Trigger an immediate suggestion scan. Rate-limited to 60 seconds and returns "
            "cached metadata when called too frequently. Requires admin access."
        ),
        inputSchema={"type": "object", "properties": {}},
    ),
]
