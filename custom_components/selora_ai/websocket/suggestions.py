"""Selora AI websocket handlers: suggestions.

Extracted from __init__.py. Handlers reach shared integration
helpers via ``from .. import`` (safe: this module is imported
lazily at registration time, after the package has loaded).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
import logging
from typing import Any

from homeassistant.components import websocket_api
from homeassistant.components.websocket_api import decorators
from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_send
import voluptuous as vol
import yaml

from .. import (
    _get_pattern_store,
    _require_admin,
    _suggestion_ignore_filter,
)
from ..automation_utils import suggestion_content_fingerprint
from ..const import (
    DOMAIN,
    SIGNAL_PROACTIVE_SUGGESTIONS,
    SUGGESTION_SCORING_TIMEOUT_INTERACTIVE,
)

_LOGGER = logging.getLogger(__name__)


@websocket_api.async_response
@decorators.websocket_command(
    {
        vol.Required("type"): "selora_ai/get_suggestions",
    }
)
async def _handle_websocket_get_suggestions(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Return proactive pattern-based suggestions (fallback to collector suggestions)."""
    if not _require_admin(connection, msg):
        return

    suggestions = hass.data.get(DOMAIN, {}).get("proactive_suggestions", [])
    if not suggestions:
        suggestions = hass.data.get(DOMAIN, {}).get("latest_suggestions", [])

    is_ignored = _suggestion_ignore_filter(hass)
    suggestions = [s for s in suggestions if not is_ignored(s)]
    connection.send_result(msg["id"], suggestions)


@websocket_api.async_response
@decorators.websocket_command(
    {
        vol.Required("type"): "selora_ai/generate_suggestions",
    }
)
async def _handle_websocket_generate_suggestions(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Trigger an on-demand LLM analysis + pattern scan to generate fresh suggestions."""
    if not _require_admin(connection, msg):
        return

    domain_data = hass.data.get(DOMAIN, {})

    # Find the runtime entry with collector and/or pattern engine
    runtime: dict[str, Any] | None = None
    for key, val in domain_data.items():
        if not isinstance(key, str) or key.startswith("_"):
            continue
        if isinstance(val, dict) and "collector" in val:
            runtime = val
            break

    if runtime is None:
        connection.send_error(msg["id"], "not_ready", "Selora AI is not fully initialized yet")
        return

    try:
        # 1. Run the fast, local-only pattern engine first (milliseconds)
        pattern_engine = runtime.get("pattern_engine")
        suggestion_generator = runtime.get("suggestion_generator")
        if pattern_engine and suggestion_generator:
            patterns = []
            try:
                async with asyncio.timeout(15):
                    patterns = await pattern_engine.scan()
            except TimeoutError:
                _LOGGER.warning("Pattern scan timed out after 15s, continuing with LLM")

            # Generation scores candidates with a bounded interactive LLM
            # timeout and falls back to confidence ranking when the LLM is
            # slow. It must NOT share the scan's short outer deadline, or a
            # slow scorer would be cancelled before the fallback can save
            # anything; the internal timeout bounds it instead.
            try:
                suggestions = await suggestion_generator.generate_from_patterns(
                    patterns, score_timeout=SUGGESTION_SCORING_TIMEOUT_INTERACTIVE
                )
                if suggestions:
                    existing = hass.data.get(DOMAIN, {}).get("proactive_suggestions", [])
                    existing.extend(suggestions)
                    hass.data[DOMAIN]["proactive_suggestions"] = existing[-50:]
                    async_dispatcher_send(hass, SIGNAL_PROACTIVE_SUGGESTIONS)
            except Exception:
                _LOGGER.exception("On-demand pattern suggestion generation failed")

        # 2. Run the LLM analysis with a shorter interactive timeout (30s)
        collector = runtime.get("collector")
        if collector:
            try:
                async with asyncio.timeout(30):
                    await collector._collect_analyze_log(force=True)
            except TimeoutError:
                _LOGGER.warning(
                    "On-demand LLM analysis timed out after 30s — returning existing suggestions"
                )

        # 3. Build set of existing automation aliases to exclude from suggestions
        existing_aliases: set[str] = set()
        for state in hass.states.async_all("automation"):
            alias = (state.attributes.get("friendly_name") or "").strip().lower()
            if alias:
                existing_aliases.add(alias)

        # 4. Return combined results: proactive first, then collector — skip existing
        #    Deduplicate by both alias AND content fingerprint (#46)
        is_ignored = _suggestion_ignore_filter(hass)
        all_suggestions = []
        seen_aliases: set[str] = set()
        seen_fingerprints: set[str] = set()
        for s in list(hass.data.get(DOMAIN, {}).get("proactive_suggestions", [])):
            alias = (s.get("alias") or "").strip().lower()
            if alias and (alias in existing_aliases or alias in seen_aliases):
                continue
            if is_ignored(s):
                continue
            auto_data = s.get("automation_data", s)
            fp = suggestion_content_fingerprint(auto_data)
            if fp in seen_fingerprints:
                continue
            all_suggestions.append(s)
            if alias:
                seen_aliases.add(alias)
            seen_fingerprints.add(fp)
        for s in hass.data.get(DOMAIN, {}).get("latest_suggestions", []):
            alias = (s.get("alias") or "").strip().lower()
            if alias and (alias in existing_aliases or alias in seen_aliases):
                continue
            if is_ignored(s):
                continue
            auto_data = s.get("automation_data", s)
            fp = suggestion_content_fingerprint(auto_data)
            if fp in seen_fingerprints:
                continue
            all_suggestions.append(s)
            if alias:
                seen_aliases.add(alias)
            seen_fingerprints.add(fp)

        connection.send_result(msg["id"], all_suggestions)
    except Exception as exc:
        _LOGGER.exception("On-demand suggestion generation failed")
        connection.send_error(msg["id"], "analysis_failed", str(exc))


@websocket_api.async_response
@decorators.websocket_command(
    {
        vol.Required("type"): "selora_ai/get_proactive_suggestions",
        vol.Optional("status", default="pending"): str,
    }
)
async def _handle_websocket_get_proactive_suggestions(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Return proactive suggestions from pattern detection."""
    if not _require_admin(connection, msg):
        return

    status = msg.get("status", "pending")
    pattern_store = _get_pattern_store(hass)
    if pattern_store:
        suggestions = await pattern_store.get_suggestions(status=status)
    else:
        all_suggestions = hass.data.get(DOMAIN, {}).get("proactive_suggestions", [])
        suggestions = [s for s in all_suggestions if s.get("status") == status]

    is_ignored = _suggestion_ignore_filter(hass)
    suggestions = [s for s in suggestions if not is_ignored(s)]
    connection.send_result(msg["id"], suggestions)


@websocket_api.async_response
@decorators.websocket_command(
    {
        vol.Required("type"): "selora_ai/update_proactive_suggestion",
        vol.Required("suggestion_id"): str,
        vol.Required("action"): vol.In(["accepted", "dismissed", "snoozed"]),
        vol.Optional("snooze_hours"): vol.Coerce(float),
    }
)
async def _handle_websocket_update_proactive_suggestion(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Accept, dismiss, or snooze a proactive suggestion."""
    if not _require_admin(connection, msg):
        return

    pattern_store = _get_pattern_store(hass)
    if not pattern_store:
        connection.send_error(msg["id"], "no_store", "Pattern store not available")
        return

    suggestion_id = msg["suggestion_id"]
    action = msg["action"]
    suggestion = await pattern_store.get_suggestion(suggestion_id)
    if suggestion is None:
        connection.send_error(msg["id"], "not_found", "Suggestion not found")
        return

    if action == "accepted":
        automation_data = suggestion.get("automation_data")
        if not isinstance(automation_data, dict) or not automation_data:
            connection.send_error(
                msg["id"], "invalid_suggestion", "Suggestion has no automation payload"
            )
            return

        from ..automation_utils import async_create_automation

        result = await async_create_automation(hass, automation_data)
        if not result.get("success", False):
            connection.send_error(
                msg["id"], "create_failed", "Failed to create automation from suggestion"
            )
            return

        await pattern_store.update_suggestion_status(suggestion_id, "accepted")
        connection.send_result(
            msg["id"],
            {
                "status": "accepted",
                "automation_created": True,
                "automation_id": result.get("automation_id"),
            },
        )
        return

    snooze_until = None
    if action == "snoozed":
        snooze_hours = msg.get("snooze_hours", 24.0)
        snooze_until = (datetime.now(UTC) + timedelta(hours=snooze_hours)).isoformat()

    updated = await pattern_store.update_suggestion_status(
        suggestion_id, action, snooze_until=snooze_until
    )
    if not updated:
        connection.send_error(msg["id"], "not_found", "Suggestion not found")
        return

    connection.send_result(msg["id"], {"status": action})


@websocket_api.async_response
@decorators.websocket_command(
    {
        vol.Required("type"): "selora_ai/get_suggestion_detail",
        vol.Required("suggestion_id"): str,
    }
)
async def _handle_websocket_get_suggestion_detail(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Return full suggestion detail with YAML preview and pattern context."""
    if not _require_admin(connection, msg):
        return

    pattern_store = _get_pattern_store(hass)
    if not pattern_store:
        connection.send_error(msg["id"], "no_store", "Pattern store not available")
        return

    suggestion = await pattern_store.get_suggestion(msg["suggestion_id"])
    if not suggestion:
        connection.send_error(msg["id"], "not_found", "Suggestion not found")
        return

    # Enrich with pattern detail if available
    pattern_id = suggestion.get("pattern_id", "")
    pattern_detail = None
    if pattern_id:
        pattern_detail = await pattern_store.get_pattern_detail(pattern_id)

    connection.send_result(
        msg["id"],
        {
            **suggestion,
            "pattern_detail": pattern_detail,
        },
    )


@websocket_api.async_response
@decorators.websocket_command(
    {
        vol.Required("type"): "selora_ai/accept_suggestion_with_edits",
        vol.Required("suggestion_id"): str,
        vol.Required("automation_yaml"): str,
    }
)
async def _handle_websocket_accept_suggestion_with_edits(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Accept a suggestion with user-edited YAML (automations tab editing)."""
    if not _require_admin(connection, msg):
        return

    pattern_store = _get_pattern_store(hass)
    if not pattern_store:
        connection.send_error(msg["id"], "no_store", "Pattern store not available")
        return

    suggestion = await pattern_store.get_suggestion(msg["suggestion_id"])
    if not suggestion:
        connection.send_error(msg["id"], "not_found", "Suggestion not found")
        return

    # Parse the user-edited YAML
    try:
        automation_data = yaml.safe_load(msg["automation_yaml"])
    except yaml.YAMLError as exc:
        connection.send_error(msg["id"], "invalid_yaml", str(exc))
        return

    if not isinstance(automation_data, dict):
        connection.send_error(msg["id"], "invalid_yaml", "YAML must be a mapping")
        return

    from ..automation_utils import async_create_automation, validate_automation_payload

    is_valid, reason, normalized = validate_automation_payload(automation_data, hass)
    if not is_valid or normalized is None:
        connection.send_error(msg["id"], "invalid_automation", reason or "Validation failed")
        return

    result = await async_create_automation(hass, normalized)
    if not result.get("success", False):
        connection.send_error(
            msg["id"], "create_failed", "Failed to create automation from suggestion"
        )
        return

    await pattern_store.update_suggestion_status(msg["suggestion_id"], "accepted")

    connection.send_result(
        msg["id"],
        {
            "status": "accepted",
            "automation_created": True,
            "automation_id": result.get("automation_id"),
        },
    )


@websocket_api.async_response
@decorators.websocket_command(
    {
        vol.Required("type"): "selora_ai/trigger_pattern_scan",
    }
)
async def _handle_websocket_trigger_pattern_scan(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Manually trigger a pattern scan (automations tab refresh)."""
    if not _require_admin(connection, msg):
        return

    domain_data = hass.data.get(DOMAIN, {})
    engine = None
    for key, val in domain_data.items():
        if key.startswith("_") or not isinstance(val, dict):
            continue
        e = val.get("pattern_engine")
        if e is not None:
            engine = e
            break

    if engine is None:
        connection.send_error(msg["id"], "no_engine", "Pattern engine not available")
        return

    new_patterns = await engine.scan()
    connection.send_result(
        msg["id"],
        {
            "patterns_found": len(new_patterns),
            "patterns": new_patterns,
        },
    )


@websocket_api.async_response
@decorators.websocket_command(
    {
        vol.Required("type"): "selora_ai/get_patterns",
        vol.Optional("status", default="active"): str,
        vol.Optional("pattern_type"): str,
    }
)
async def _handle_websocket_get_patterns(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Return detected patterns for the automations tab with filtering."""
    if not _require_admin(connection, msg):
        return

    pattern_store = _get_pattern_store(hass)
    if not pattern_store:
        connection.send_error(msg["id"], "no_store", "Pattern store not available")
        return
    patterns = await pattern_store.get_patterns(
        status=msg.get("status"),
        pattern_type=msg.get("pattern_type"),
    )
    # Sort by confidence descending
    patterns.sort(key=lambda p: p.get("confidence", 0), reverse=True)
    connection.send_result(msg["id"], patterns)


@websocket_api.async_response
@decorators.websocket_command(
    {
        vol.Required("type"): "selora_ai/get_pattern_detail",
        vol.Required("pattern_id"): str,
    }
)
async def _handle_websocket_get_pattern_detail(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Return a single pattern with full entity history context."""
    if not _require_admin(connection, msg):
        return

    pattern_store = _get_pattern_store(hass)
    if not pattern_store:
        connection.send_error(msg["id"], "no_store", "Pattern store not available")
        return
    detail = await pattern_store.get_pattern_detail(msg["pattern_id"])
    if detail is None:
        connection.send_error(msg["id"], "not_found", "Pattern not found")
        return
    connection.send_result(msg["id"], detail)


@websocket_api.async_response
@decorators.websocket_command(
    {
        vol.Required("type"): "selora_ai/update_pattern_status",
        vol.Required("pattern_id"): str,
        vol.Required("status"): vol.In(["active", "dismissed", "snoozed"]),
        vol.Optional("snooze_hours", default=24): int,
    }
)
async def _handle_websocket_update_pattern_status(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Update a pattern's status (dismiss or snooze)."""
    if not _require_admin(connection, msg):
        return

    pattern_store = _get_pattern_store(hass)
    if not pattern_store:
        connection.send_error(msg["id"], "no_store", "Pattern store not available")
        return

    snooze_until = None
    if msg["status"] == "snoozed":
        snooze_until = (
            datetime.now(UTC) + timedelta(hours=msg.get("snooze_hours", 24))
        ).isoformat()

    ok = await pattern_store.update_pattern_status(msg["pattern_id"], msg["status"], snooze_until)
    if not ok:
        connection.send_error(msg["id"], "not_found", "Pattern not found")
        return
    connection.send_result(msg["id"], {"status": msg["status"]})


def async_register(hass: HomeAssistant) -> None:
    """Register the suggestions websocket commands."""
    from homeassistant.components import websocket_api

    websocket_api.async_register_command(hass, _handle_websocket_get_suggestions)
    websocket_api.async_register_command(hass, _handle_websocket_generate_suggestions)
    websocket_api.async_register_command(hass, _handle_websocket_get_proactive_suggestions)
    websocket_api.async_register_command(hass, _handle_websocket_update_proactive_suggestion)
    websocket_api.async_register_command(hass, _handle_websocket_get_patterns)
    websocket_api.async_register_command(hass, _handle_websocket_get_pattern_detail)
    websocket_api.async_register_command(hass, _handle_websocket_update_pattern_status)
    websocket_api.async_register_command(hass, _handle_websocket_get_suggestion_detail)
    websocket_api.async_register_command(hass, _handle_websocket_accept_suggestion_with_edits)
    websocket_api.async_register_command(hass, _handle_websocket_trigger_pattern_scan)
