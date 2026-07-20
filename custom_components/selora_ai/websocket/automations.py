"""Selora AI websocket handlers: automations.

Extracted from __init__.py. Handlers reach shared integration
helpers via ``from .. import`` (safe: this module is imported
lazily at registration time, after the package has loaded).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from homeassistant.components import websocket_api
from homeassistant.components.websocket_api import decorators
from homeassistant.core import HomeAssistant
import voluptuous as vol
import yaml

from .. import (
    _collect_entity_states,
    _find_llm,
    _get_automation_store,
    _require_admin,
)
from ..const import (
    AUTOMATION_ID_PREFIX,
    DOMAIN,
    SELORA_AI_LABEL_ID,
)
from ..conversation_store import ConversationStore

_LOGGER = logging.getLogger(__name__)


@websocket_api.async_response
@decorators.websocket_command(
    {
        vol.Required("type"): "selora_ai/set_automation_status",
        vol.Required("session_id"): str,
        vol.Required("message_index"): int,
        vol.Required("status"): str,
        vol.Optional("automation_id"): str,
    }
)
async def _handle_websocket_set_automation_status(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Update the acceptance status of an automation proposal in a session."""
    if not _require_admin(connection, msg):
        return

    valid_statuses = {"pending", "accepted", "declined", "saved", "refining"}
    if msg["status"] not in valid_statuses:
        connection.send_error(
            msg["id"], "invalid_status", f"Status must be one of {valid_statuses}"
        )
        return

    store: ConversationStore = hass.data[DOMAIN].setdefault("_conv_store", ConversationStore(hass))
    ok = await store.set_automation_status(
        msg["session_id"],
        msg["message_index"],
        msg["status"],
        automation_id=msg.get("automation_id"),
    )
    if not ok:
        connection.send_error(msg["id"], "not_found", "Session or message not found")
        return
    connection.send_result(msg["id"], {"status": "updated"})


@websocket_api.async_response
@decorators.websocket_command(
    {
        vol.Required("type"): "selora_ai/create_automation",
        vol.Required("automation"): dict,
        vol.Optional("session_id"): str,
    }
)
async def _handle_websocket_create_automation(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Create a new automation from the side panel."""
    if not _require_admin(connection, msg):
        return

    automation_data = msg["automation"]

    # Basic validation
    has_trigger = automation_data.get("trigger") or automation_data.get("triggers")
    has_action = automation_data.get("action") or automation_data.get("actions")

    if not automation_data.get("alias") or not has_trigger or not has_action:
        connection.send_error(
            msg["id"],
            "invalid_format",
            "Invalid automation structure (missing alias, trigger, or action)",
        )
        return

    try:
        from ..automation_utils import async_create_automation

        # Per project policy automations are always written disabled —
        # the user must review and enable them in Home Assistant. We
        # still surface risk_level so the panel can flag elevated-risk
        # payloads (shell_command, webhook trigger, etc.) for extra
        # caution before the user toggles them on.
        result = await async_create_automation(
            hass, automation_data, session_id=msg.get("session_id")
        )
        if result["success"]:
            connection.send_result(
                msg["id"],
                {
                    "status": "success",
                    "automation_id": result["automation_id"],
                    "risk_level": result.get("risk_level", "normal"),
                },
            )
        else:
            connection.send_error(
                msg["id"], "creation_failed", "Failed to write automation to file"
            )
    except Exception as exc:
        _LOGGER.exception("Error creating automation")
        connection.send_error(msg["id"], "error", str(exc))


@websocket_api.async_response
@decorators.websocket_command(
    {
        vol.Required("type"): "selora_ai/apply_automation_yaml",
        vol.Required("yaml_text"): str,
        vol.Optional("session_id"): str,
        vol.Optional("automation_id"): str,
        # General create-or-update: an update honors the submitted `initial_state`
        # by default. Refinement callers whose YAML may carry a stale value opt IN
        # to preservation by sending True (mirrors update_automation_yaml).
        vol.Optional("preserve_enabled_state"): bool,
    }
)
async def _handle_websocket_apply_automation_yaml(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Parse raw YAML text and create or update an automation."""
    if not _require_admin(connection, msg):
        return

    from ..automation_utils import (
        _parse_automation_yaml,
        async_create_automation,
        async_update_automation,
    )

    parsed = await hass.async_add_executor_job(_parse_automation_yaml, msg["yaml_text"])
    if parsed is None:
        connection.send_error(msg["id"], "parse_error", "Invalid YAML — could not parse automation")
        return

    has_trigger = parsed.get("trigger") or parsed.get("triggers")
    has_action = parsed.get("action") or parsed.get("actions")
    if not parsed.get("alias") or not has_trigger or not has_action:
        connection.send_error(
            msg["id"], "invalid_format", "Automation must have alias, trigger, and action"
        )
        return

    automation_id = msg.get("automation_id")
    try:
        if automation_id:
            success = await async_update_automation(
                hass,
                automation_id,
                parsed,
                session_id=msg.get("session_id"),
                version_message="Refined via chat",
                # Authoritative by default (honor submitted initial_state); refinement
                # callers opt in to preservation. Keeps this general create-or-update
                # endpoint working for non-chat/older clients that change the state.
                preserve_enabled_state=msg.get("preserve_enabled_state", False),
            )
            if success:
                connection.send_result(msg["id"], {"status": "updated"})
            else:
                connection.send_error(
                    msg["id"], "not_found", "Automation not found in automations.yaml"
                )
        else:
            # All chat-driven creates land disabled per project policy —
            # the user must enable manually after review. Pass risk_level
            # back so the panel can flag elevated-risk YAML.
            result = await async_create_automation(hass, parsed, session_id=msg.get("session_id"))
            if result["success"]:
                connection.send_result(
                    msg["id"],
                    {
                        "status": "created",
                        "automation_id": result["automation_id"],
                        "risk_level": result.get("risk_level", "normal"),
                    },
                )
            else:
                connection.send_error(msg["id"], "creation_failed", "Failed to write automation")
    except Exception as exc:
        _LOGGER.exception("Error applying automation YAML")
        connection.send_error(msg["id"], "error", str(exc))


@websocket_api.async_response
@decorators.websocket_command(
    {
        vol.Required("type"): "selora_ai/update_automation_yaml",
        vol.Required("automation_id"): str,
        vol.Required("yaml_text"): str,
        vol.Optional("session_id"): str,
        vol.Optional("version_message"): str,
        # This endpoint is an authoritative YAML update by default (its historical
        # contract): a submitted `initial_state` is honored. Chat refinements — whose
        # YAML may carry a stale `initial_state` — opt IN to preservation by sending
        # True. Defaulting to authoritative keeps older/cached panel clients that
        # change `initial_state` working across an integration upgrade.
        vol.Optional("preserve_enabled_state"): bool,
    }
)
async def _handle_websocket_update_automation_yaml(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Parse raw YAML text and update an existing automation in automations.yaml."""
    if not _require_admin(connection, msg):
        return

    from ..automation_utils import _parse_automation_yaml, async_update_automation

    parsed = await hass.async_add_executor_job(_parse_automation_yaml, msg["yaml_text"])
    if parsed is None:
        connection.send_error(msg["id"], "parse_error", "Invalid YAML — could not parse automation")
        return

    try:
        success = await async_update_automation(
            hass,
            msg["automation_id"],
            parsed,
            session_id=msg.get("session_id"),
            version_message=msg.get("version_message", "Updated via YAML editor"),
            preserve_enabled_state=msg.get("preserve_enabled_state", False),
        )
        if success:
            connection.send_result(msg["id"], {"status": "updated"})
        else:
            connection.send_error(
                msg["id"], "not_found", "Automation not found in automations.yaml"
            )
    except Exception as exc:
        _LOGGER.exception("Error updating automation YAML")
        connection.send_error(msg["id"], "error", str(exc))


@websocket_api.async_response
@decorators.websocket_command(
    {
        vol.Required("type"): "selora_ai/rename_automation",
        vol.Required("automation_id"): str,
        vol.Required("alias"): str,
    }
)
async def _handle_websocket_rename_automation(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Rename an existing automation's alias in automations.yaml and reload."""
    if not _require_admin(connection, msg):
        return

    from pathlib import Path

    from ..automation_utils import (
        AUTOMATIONS_YAML_LOCK,
        _read_automations_yaml,
        _write_automations_yaml,
    )

    automation_id = msg["automation_id"]
    new_alias = msg["alias"].strip()
    if not new_alias:
        connection.send_error(msg["id"], "invalid", "Alias cannot be empty")
        return

    automations_path = Path(hass.config.config_dir) / "automations.yaml"
    try:
        # Hold the shared lock for the full read/modify/write/reload span so a
        # concurrent collector run or chat-driven create cannot interleave.
        async with AUTOMATIONS_YAML_LOCK:
            existing = await hass.async_add_executor_job(_read_automations_yaml, automations_path)
            found = False
            for a in existing:
                if a.get("id") == automation_id:
                    a["alias"] = new_alias
                    found = True
                    break
            if not found:
                connection.send_error(msg["id"], "not_found", "Automation not found")
                return
            await hass.async_add_executor_job(_write_automations_yaml, automations_path, existing)
            await hass.services.async_call("automation", "reload", blocking=True)
        connection.send_result(msg["id"], {"status": "renamed"})
    except Exception as exc:
        _LOGGER.exception("Error renaming automation")
        connection.send_error(msg["id"], "error", str(exc))


@websocket_api.async_response
@decorators.websocket_command(
    {
        vol.Required("type"): "selora_ai/quick_create_automation",
        vol.Required("name"): str,
    }
)
async def _handle_websocket_quick_create_automation(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Create an automation by name using the LLM — no chat session needed."""
    if not _require_admin(connection, msg):
        return

    name = msg["name"].strip()
    if not name:
        connection.send_error(msg["id"], "invalid_name", "Automation name is required")
        return

    llm = _find_llm(hass)
    if llm is None:
        connection.send_error(msg["id"], "no_llm", "No LLM provider configured")
        return

    try:
        entities = _collect_entity_states(hass)
        existing = [
            {
                "entity_id": s.entity_id,
                "alias": s.attributes.get("friendly_name", s.entity_id),
                "state": s.state,
            }
            for s in hass.states.async_all("automation")
        ]

        prompt = (
            f'Create a Home Assistant automation called "{name}". '
            "Infer the best trigger, conditions, and actions based on the name "
            "and the available entities. Keep it practical and useful. "
            "IMPORTANT: All trigger fields must have valid values — never use null. "
            "For time triggers use 'at' with HH:MM:SS format. "
            "For state triggers use string values for 'to'/'from'."
        )

        async with asyncio.timeout(30):
            result = await llm.architect_chat(prompt, entities, existing_automations=existing)

        automation = result.get("automation")
        if not automation:
            # Try parsing from response text
            parsed = llm.parse_streamed_response(
                result.get("response", ""), entities, user_message=prompt
            )
            automation = parsed.get("automation")

        if not automation:
            connection.send_error(
                msg["id"],
                "no_automation",
                "The AI could not generate an automation for that name. Try a more descriptive name.",
            )
            return

        # Ensure the alias matches what the user typed
        automation["alias"] = name

        # Validate before saving — reject broken automations
        from ..automation_utils import async_create_automation, validate_automation_payload

        is_valid, reason, normalized = validate_automation_payload(automation, hass)
        if not is_valid or normalized is None:
            connection.send_error(
                msg["id"],
                "invalid_automation",
                f"Generated automation is invalid: {reason}. Try a more specific name.",
            )
            return

        # Sanitize triggers — strip null values that HA rejects
        triggers = normalized.get("triggers") or []
        for t in triggers if isinstance(triggers, list) else [triggers]:
            for key in list(t.keys()):
                if t[key] is None:
                    del t[key]

        # Per project policy all chat-driven automations land disabled and
        # must be reviewed/enabled by the user. The pre-MR quick-create code
        # auto-enabled by setting initial_state=True; that path was a
        # carve-out from the disabled-by-default rule and is now closed.
        create_result = await async_create_automation(hass, normalized)

        if create_result.get("success"):
            connection.send_result(
                msg["id"],
                {
                    "status": "created",
                    "automation_id": create_result["automation_id"],
                },
            )
        else:
            connection.send_error(msg["id"], "create_failed", "Failed to save automation")

    except TimeoutError:
        connection.send_error(msg["id"], "timeout", "Automation creation timed out")
    except Exception as exc:
        _LOGGER.exception("Quick create automation failed")
        connection.send_error(msg["id"], "error", str(exc))


@websocket_api.async_response
@decorators.websocket_command(
    {
        vol.Required("type"): "selora_ai/create_draft",
        vol.Required("alias"): str,
        vol.Required("session_id"): str,
    }
)
async def _handle_websocket_create_draft(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Create a persisted draft automation linked to a chat session."""
    if not _require_admin(connection, msg):
        return

    store = _get_automation_store(hass)
    draft = await store.create_draft(msg["alias"], msg["session_id"])
    connection.send_result(msg["id"], draft)


@websocket_api.async_response
@decorators.websocket_command(
    {
        vol.Required("type"): "selora_ai/get_drafts",
    }
)
async def _handle_websocket_get_drafts(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Return all draft automations."""
    if not _require_admin(connection, msg):
        return

    store = _get_automation_store(hass)
    drafts = await store.list_drafts()
    connection.send_result(msg["id"], drafts)


@websocket_api.async_response
@decorators.websocket_command(
    {
        vol.Required("type"): "selora_ai/remove_draft",
        vol.Required("draft_id"): str,
    }
)
async def _handle_websocket_remove_draft(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Remove a draft automation."""
    if not _require_admin(connection, msg):
        return

    store = _get_automation_store(hass)
    ok = await store.remove_draft(msg["draft_id"])
    if ok:
        connection.send_result(msg["id"], {"status": "ok"})
    else:
        connection.send_error(msg["id"], "not_found", "Draft not found")


@websocket_api.async_response
@decorators.websocket_command(
    {
        vol.Required("type"): "selora_ai/get_automations",
    }
)
async def _handle_websocket_get_automations(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Return all existing automations and flag Selora-managed ones, including full config.

    Each automation is enriched with AutomationStore metadata when available:
    version_count, current_version_id.
    """
    if not _require_admin(connection, msg):
        return

    try:
        from pathlib import Path

        from homeassistant.helpers import entity_registry as er

        from ..automation_utils import _read_automations_yaml

        registry = er.async_get(hass)

        # Build a lookup from automation id → full config for flowchart rendering
        automations_path = Path(hass.config.config_dir) / "automations.yaml"
        yaml_automations = await hass.async_add_executor_job(
            _read_automations_yaml, automations_path
        )
        yaml_by_id: dict[str, dict[str, Any]] = {
            str(a.get("id")): a for a in yaml_automations if a.get("id")
        }
        yaml_by_alias: dict[str, dict[str, Any]] = {
            str(a.get("alias", "")): a for a in yaml_automations if a.get("id") and a.get("alias")
        }

        store = _get_automation_store(hass)

        # Map each automation back to the recipe that installed it (if any),
        # so the panel can badge recipe-owned rows and explain why they're
        # not editable in-app.
        from ..recipes.attribution import async_build_recipe_attribution

        attribution = await async_build_recipe_attribution(hass)

        automations = []

        for state in hass.states.async_all("automation"):
            entity_id = state.entity_id
            entry = registry.async_get(entity_id)

            is_selora = False
            automation_id = ""
            if entry and entry.unique_id:
                unique_id = str(entry.unique_id)
                is_selora = unique_id.startswith(AUTOMATION_ID_PREFIX)
                if unique_id in yaml_by_id:
                    automation_id = unique_id

            # Detection paths, in order: entity-registry label, the
            # YAML id prefix exposed in state attributes, and the
            # legacy ``[Selora AI]`` description marker for
            # pre-label automations.
            if not is_selora and entry and entry.labels and SELORA_AI_LABEL_ID in entry.labels:
                is_selora = True

            description = state.attributes.get("description") or ""
            if not is_selora and description and "[Selora AI]" in description:
                is_selora = True

            if not automation_id:
                state_id = state.attributes.get("id")
                if state_id is not None:
                    state_id_str = str(state_id)
                    if state_id_str in yaml_by_id:
                        automation_id = state_id_str
                    if not is_selora and state_id_str.startswith(AUTOMATION_ID_PREFIX):
                        is_selora = True

            if not automation_id and entry and entry.unique_id:
                unique_id_attr = state.attributes.get("unique_id")
                if unique_id_attr is not None:
                    unique_id_attr_str = str(unique_id_attr)
                    if unique_id_attr_str in yaml_by_id:
                        automation_id = unique_id_attr_str

            # Last fallback: alias match from automations.yaml
            if not automation_id:
                alias = str(state.attributes.get("friendly_name", ""))
                alias_match = yaml_by_alias.get(alias)
                if alias_match:
                    automation_id = str(alias_match.get("id", ""))

            # Merge full config (trigger/condition/action) from automations.yaml
            full_config = yaml_by_id.get(automation_id, {})

            # Serialise config as editable YAML text (omit internal HA fields)
            yaml_text = ""
            if full_config:
                try:
                    yaml_text = yaml.dump(full_config, allow_unicode=True, default_flow_style=False)
                except Exception:
                    yaml_text = ""

            # Merge lifecycle metadata from the store
            meta = await store.get_metadata(automation_id) if automation_id else None

            # Recipe attribution: match on the automation's YAML id first
            # (stable), then its friendly name / alias.
            recipe_ref = None
            state_id_attr = state.attributes.get("id")
            if state_id_attr is not None:
                recipe_ref = attribution["automations_by_id"].get(str(state_id_attr))
            if recipe_ref is None:
                recipe_ref = attribution["automations_by_alias"].get(
                    str(state.attributes.get("friendly_name", ""))
                )

            automations.append(
                {
                    "entity_id": entity_id,
                    "automation_id": automation_id,
                    "recipe_slug": recipe_ref["slug"] if recipe_ref else "",
                    "recipe_title": recipe_ref["title"] if recipe_ref else "",
                    "alias": state.attributes.get("friendly_name", entity_id),
                    "description": description or full_config.get("description", ""),
                    "state": state.state,
                    "is_selora": is_selora,
                    "last_triggered": state.attributes.get("last_triggered"),
                    "last_updated": state.last_updated.isoformat() if state.last_updated else None,
                    "persisted_enabled": (
                        full_config.get("initial_state")
                        if isinstance(full_config.get("initial_state"), bool)
                        else None
                    ),
                    "triggers": full_config.get("triggers") or full_config.get("trigger") or [],
                    "conditions": full_config.get("conditions")
                    or full_config.get("condition")
                    or [],
                    "actions": full_config.get("actions") or full_config.get("action") or [],
                    "yaml_text": yaml_text,
                    # Lifecycle metadata (None for automations not tracked by the store)
                    "version_count": meta["version_count"] if meta else None,
                    "current_version_id": meta["current_version_id"] if meta else None,
                }
            )

        # Sort by position in automations.yaml (newest appended last)
        yaml_order = {str(a.get("id")): i for i, a in enumerate(yaml_automations) if a.get("id")}
        automations.sort(key=lambda a: yaml_order.get(a["automation_id"], -1))

        connection.send_result(msg["id"], automations)
    except Exception as exc:
        _LOGGER.exception("Error in _handle_websocket_get_automations")
        connection.send_error(msg["id"], "unknown_error", str(exc))


@websocket_api.async_response
@decorators.websocket_command(
    {
        vol.Required("type"): "selora_ai/get_automation_versions",
        vol.Required("automation_id"): str,
    }
)
async def _handle_websocket_get_automation_versions(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Return ordered version history for an automation with session previews joined in."""
    if not _require_admin(connection, msg):
        return

    try:
        store = _get_automation_store(hass)
        versions = await store.get_versions(msg["automation_id"])

        conv_store: ConversationStore = hass.data[DOMAIN].setdefault(
            "_conv_store", ConversationStore(hass)
        )
        result = []
        for v in versions:
            entry = dict(v)
            sid = v.get("session_id")
            entry["session_preview"] = await conv_store.get_session_preview(sid) if sid else None
            result.append(entry)

        connection.send_result(msg["id"], result)
    except Exception as exc:
        _LOGGER.exception("Error in get_automation_versions")
        connection.send_error(msg["id"], "unknown_error", str(exc))


@websocket_api.async_response
@decorators.websocket_command(
    {
        vol.Required("type"): "selora_ai/get_automation_diff",
        vol.Required("automation_id"): str,
        vol.Required("version_id_a"): str,
        vol.Required("version_id_b"): str,
    }
)
async def _handle_websocket_get_automation_diff(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Return a unified diff between two versions of an automation."""
    if not _require_admin(connection, msg):
        return

    try:
        store = _get_automation_store(hass)
        diff = await store.get_diff(msg["automation_id"], msg["version_id_a"], msg["version_id_b"])
        if diff is None:
            connection.send_error(msg["id"], "not_found", "One or both version IDs not found")
            return
        connection.send_result(msg["id"], {"diff": diff})
    except Exception as exc:
        _LOGGER.exception("Error in get_automation_diff")
        connection.send_error(msg["id"], "unknown_error", str(exc))


@websocket_api.async_response
@decorators.websocket_command(
    {
        vol.Required("type"): "selora_ai/toggle_automation",
        vol.Required("automation_id"): str,
        vol.Required("entity_id"): str,
        vol.Optional("enabled"): bool,
    }
)
async def _handle_websocket_toggle_automation(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Toggle an automation's enabled state, persisting initial_state in automations.yaml."""
    if not _require_admin(connection, msg):
        return

    try:
        from ..automation_utils import async_toggle_automation

        entity_id = msg["entity_id"]
        requested_enabled = msg.get("enabled")
        if requested_enabled is None:
            state = hass.states.get(entity_id)
            currently_on = state and state.state == "on"
            enable = not currently_on
            status = "toggled"
        else:
            enable = bool(requested_enabled)
            status = "set"
        success = await async_toggle_automation(hass, msg["automation_id"], entity_id, enable)
        if success:
            connection.send_result(msg["id"], {"status": status, "enabled": enable})
        else:
            connection.send_error(msg["id"], "not_found", "Automation not found")
    except Exception as exc:
        _LOGGER.exception("Error in toggle_automation")
        connection.send_error(msg["id"], "unknown_error", str(exc))


@websocket_api.async_response
@decorators.websocket_command(
    {
        vol.Required("type"): "selora_ai/delete_automation",
        vol.Required("automation_id"): str,
    }
)
async def _handle_websocket_delete_automation(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Permanently delete a Selora-managed automation and all version history."""
    if not _require_admin(connection, msg):
        return

    try:
        from pathlib import Path

        from ..automation_utils import _read_automations_yaml

        automation_id = msg["automation_id"]

        # Only allow deletion of Selora-managed automations
        automations_path = Path(hass.config.config_dir) / "automations.yaml"
        yaml_automations = await hass.async_add_executor_job(
            _read_automations_yaml, automations_path
        )
        target = next((a for a in yaml_automations if a.get("id") == automation_id), None)
        if not target:
            connection.send_error(msg["id"], "not_found", "Automation not found")
            return

        from ..helpers import is_selora_automation

        if not is_selora_automation(target):
            connection.send_error(
                msg["id"], "not_allowed", "Only Selora-managed automations can be deleted"
            )
            return

        from ..automation_utils import async_delete_automation

        success = await async_delete_automation(hass, automation_id)
        if success:
            connection.send_result(msg["id"], {"status": "deleted"})
        else:
            connection.send_error(msg["id"], "not_found", "Automation not found")
    except Exception as exc:
        _LOGGER.exception("Error in delete_automation")
        connection.send_error(msg["id"], "unknown_error", str(exc))


@websocket_api.async_response
@decorators.websocket_command(
    {
        vol.Required("type"): "selora_ai/get_automation_lineage",
        vol.Required("automation_id"): str,
    }
)
async def _handle_websocket_get_automation_lineage(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Return the lineage list for an automation with session previews joined in."""
    if not _require_admin(connection, msg):
        return

    try:
        store = _get_automation_store(hass)
        lineage = await store.get_automation_lineage(msg["automation_id"])

        conv_store: ConversationStore = hass.data[DOMAIN].setdefault(
            "_conv_store", ConversationStore(hass)
        )
        result = []
        for entry in lineage:
            enriched = dict(entry)
            sid = entry.get("session_id")
            if sid:
                session = await conv_store.get_session(sid)
                enriched["session_preview"] = await conv_store.get_session_preview(sid)
                enriched["session_title"] = (session or {}).get("title") or enriched[
                    "session_preview"
                ]
            else:
                enriched["session_preview"] = None
                enriched["session_title"] = None
            result.append(enriched)

        connection.send_result(msg["id"], result)
    except Exception as exc:
        _LOGGER.exception("Error in get_automation_lineage")
        connection.send_error(msg["id"], "unknown_error", str(exc))


@websocket_api.async_response
@decorators.websocket_command(
    {
        vol.Required("type"): "selora_ai/get_session_automations",
        vol.Required("session_id"): str,
    }
)
async def _handle_websocket_get_session_automations(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Return all automations (with metadata) touched by a given session."""
    if not _require_admin(connection, msg):
        return

    try:
        store = _get_automation_store(hass)
        automation_ids = await store.get_session_automations(msg["session_id"])

        result = []
        for automation_id in automation_ids:
            meta = await store.get_metadata(automation_id)
            if meta:
                result.append(meta)

        connection.send_result(msg["id"], result)
    except Exception as exc:
        _LOGGER.exception("Error in get_session_automations")
        connection.send_error(msg["id"], "unknown_error", str(exc))


@websocket_api.async_response
@decorators.websocket_command(
    {
        vol.Required("type"): "selora_ai/load_automation_to_session",
        vol.Required("automation_id"): str,
        vol.Optional("session_id"): str,
    }
)
async def _handle_websocket_load_automation_to_session(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Open a chat session pre-loaded with the automation's current YAML for refinement.

    Inserts an assistant message that surfaces the automation YAML as context.
    Subsequent selora_ai/chat messages in this session refine it via the existing
    chat handler.
    """
    if not _require_admin(connection, msg):
        return

    from pathlib import Path

    from ..automation_utils import _parse_automation_yaml, _read_automations_yaml

    automation_id = msg["automation_id"]

    # Resolve YAML — prefer the store's current version, fall back to live file
    store = _get_automation_store(hass)
    record = await store.get_record(automation_id)

    automation_data: dict[str, Any] | None = None
    yaml_text: str = ""

    if record:
        versions = record.get("versions", [])
        if versions:
            current_ver = next(
                (v for v in versions if v["version_id"] == record["current_version_id"]),
                versions[-1],
            )
            yaml_text = current_ver.get("yaml", "")
            version_data = current_ver.get("data")
            if isinstance(version_data, dict):
                automation_data = version_data

    if yaml_text and automation_data is None:
        parsed = await hass.async_add_executor_job(_parse_automation_yaml, yaml_text)
        if parsed:
            automation_data = parsed

    if not yaml_text:
        # Fall back to reading live automations.yaml
        automations_path = Path(hass.config.config_dir) / "automations.yaml"
        existing = await hass.async_add_executor_job(_read_automations_yaml, automations_path)
        for a in existing:
            if a.get("id") == automation_id:
                automation_data = a
                yaml_text = yaml.dump(a, allow_unicode=True, default_flow_style=False)
                break

    if not yaml_text or automation_data is None:
        connection.send_error(msg["id"], "not_found", "Automation not found")
        return

    conv_store: ConversationStore = hass.data[DOMAIN].setdefault(
        "_conv_store", ConversationStore(hass)
    )

    # Resolve or create session
    session_id = msg.get("session_id", "")
    if session_id:
        session = await conv_store.get_session(session_id)
        if not session:
            session = await conv_store.create_session()
            session_id = session["id"]
    else:
        session = await conv_store.create_session()
        session_id = session["id"]

    alias = automation_data.get("alias", automation_id)

    # Insert assistant context message so the LLM has the full automation on first turn
    await conv_store.append_message(
        session_id,
        "assistant",
        "Describe the changes",
        intent="automation",
        automation=automation_data,
        automation_yaml=yaml_text,
        automation_status="refining",
        automation_id=automation_id,
    )

    connection.send_result(
        msg["id"],
        {
            "session_id": session_id,
            "automation_id": automation_id,
            "alias": alias,
            "yaml_text": yaml_text,
        },
    )


def async_register(hass: HomeAssistant) -> None:
    """Register the automations websocket commands."""
    from homeassistant.components import websocket_api

    websocket_api.async_register_command(hass, _handle_websocket_create_automation)
    websocket_api.async_register_command(hass, _handle_websocket_apply_automation_yaml)
    websocket_api.async_register_command(hass, _handle_websocket_update_automation_yaml)
    websocket_api.async_register_command(hass, _handle_websocket_rename_automation)
    websocket_api.async_register_command(hass, _handle_websocket_get_automations)
    websocket_api.async_register_command(hass, _handle_websocket_get_automation_versions)
    websocket_api.async_register_command(hass, _handle_websocket_get_automation_diff)
    websocket_api.async_register_command(hass, _handle_websocket_toggle_automation)
    websocket_api.async_register_command(hass, _handle_websocket_delete_automation)
    websocket_api.async_register_command(hass, _handle_websocket_get_automation_lineage)
    websocket_api.async_register_command(hass, _handle_websocket_get_session_automations)
    websocket_api.async_register_command(hass, _handle_websocket_load_automation_to_session)
    websocket_api.async_register_command(hass, _handle_websocket_set_automation_status)
    websocket_api.async_register_command(hass, _handle_websocket_create_draft)
    websocket_api.async_register_command(hass, _handle_websocket_get_drafts)
    websocket_api.async_register_command(hass, _handle_websocket_remove_draft)
    websocket_api.async_register_command(hass, _handle_websocket_quick_create_automation)
