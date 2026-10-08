"""MCP tools for automations: list, read, validate, create/replace, enable, delete, trigger."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from homeassistant.core import HomeAssistant

from ..helpers import attach_previous
from .common import (
    _get_automation_store,
    _is_pending_automation,
    _is_selora,
    _read_yaml_automations,
    _resolve_automation,
    _resolve_yaml_automation_entity_id,
    _sanitize,
    _sanitize_risk,
)

if TYPE_CHECKING:
    from ..automation_store import AutomationStore
    from ..types import (
        AutomationDict,
        AutomationMetadata,
        AutomationRecord,
        RiskAssessment,
    )


_LOGGER = logging.getLogger(__name__)


# ── Tool: selora_list_automations ─────────────────────────────────────────────


async def _tool_list_automations(
    hass: HomeAssistant, arguments: dict[str, Any]
) -> list[dict[str, Any]]:
    """List all Home Assistant automations (yaml + storage-managed).

    Each entry exposes whether the automation is Selora-managed (``selora_managed``)
    and its source (``yaml`` for entries in ``automations.yaml``, ``storage`` for
    UI/integration-managed). Selora-managed entries also carry version metadata
    and a risk assessment; the rest leave those fields null.
    """
    from ..automation_utils import assess_automation_risk

    status_filter: str | None = arguments.get("status")
    selora_only: bool = bool(arguments.get("selora_only", False))

    yaml_automations: list[dict[str, Any]] = await _read_yaml_automations(hass)
    yaml_by_id: dict[str, dict[str, Any]] = {
        str(a["id"]): a for a in yaml_automations if a.get("id") is not None
    }
    # Build an entity_id-keyed yaml index so id-less yaml entries (which HA
    # still loads as automations via their alias slug) are recognised as
    # yaml-managed instead of falling through to ``source: storage``.
    yaml_by_entity_id: dict[str, dict[str, Any]] = {}
    for entry in yaml_automations:
        if not isinstance(entry, dict):
            continue
        eid = _resolve_yaml_automation_entity_id(hass, entry)
        if eid:
            yaml_by_entity_id[eid] = entry
    store: AutomationStore = _get_automation_store(hass)

    result: list[dict[str, Any]] = []
    for state in hass.states.async_all("automation"):
        automation_id = str(state.attributes.get("id", ""))
        alias_raw = state.attributes.get("friendly_name") or state.entity_id.split(".", 1)[-1]
        yaml_entry = (
            yaml_by_id.get(automation_id) if automation_id else None
        ) or yaml_by_entity_id.get(state.entity_id)
        is_selora = _is_selora(yaml_entry) if yaml_entry else False

        if selora_only and not is_selora:
            continue

        record: AutomationRecord | None = (
            await store.get_record(automation_id) if (automation_id and is_selora) else None
        )
        meta: AutomationMetadata | None = (
            await store.get_metadata(automation_id) if (automation_id and is_selora) else None
        )

        if is_selora and yaml_entry and _is_pending_automation(yaml_entry, record):
            status = "pending"
        else:
            status = "enabled" if state.state == "on" else "disabled"

        if status_filter and status != status_filter:
            continue

        risk: RiskAssessment | None = assess_automation_risk(yaml_entry) if yaml_entry else None

        result.append(
            {
                "automation_id": automation_id,
                "entity_id": state.entity_id,
                "alias": _sanitize(alias_raw),
                "status": status,
                "selora_managed": is_selora,
                "source": "yaml" if yaml_entry else "storage",
                "version_count": meta["version_count"] if meta else None,
                "current_version_id": meta["current_version_id"] if meta else None,
                "risk_assessment": _sanitize_risk(risk) if risk else None,
            }
        )

    return result


# ── Tool: selora_get_automation ───────────────────────────────────────────────


async def _tool_get_automation(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Return full detail for any HA automation (yaml or storage-managed).

    Accepts either ``automation_id`` (the id from ``state.attributes['id']``) or
    ``entity_id`` (e.g. ``automation.morning_routine``). YAML, version history,
    and risk assessment are populated when the automation is yaml-managed and
    Selora-tracked; otherwise those fields are empty/null.
    """
    import yaml as _yaml

    from ..automation_utils import assess_automation_risk

    automation_id: str = str(arguments.get("automation_id", "")).strip()
    entity_id_arg: str = str(arguments.get("entity_id", "")).strip()
    if not automation_id and not entity_id_arg:
        return {"error": "automation_id or entity_id is required"}

    state, resolved_id, resolved_entity = _resolve_automation(
        hass, automation_id=automation_id, entity_id=entity_id_arg
    )
    # An unloaded YAML entry resolves to empty ids; the id asked for is then
    # the only handle on it (as in ``_preview_delete_automation``).
    automation_id = resolved_id or automation_id
    entity_id = resolved_entity or entity_id_arg

    yaml_automations: list[dict[str, Any]] = await _read_yaml_automations(hass)
    # Fall back to a yaml-only lookup when the automation isn't in the state
    # machine — covers automations that exist in automations.yaml but failed
    # to load (bad manual edit, missing referenced entity, reload error).
    if state is None:
        if not automation_id:
            return {"error": f"Automation {_sanitize(entity_id_arg)} not found"}
        yaml_only = next(
            (a for a in yaml_automations if str(a.get("id")) == automation_id),
            None,
        )
        if yaml_only is None:
            return {"error": f"Automation {_sanitize(automation_id)} not found"}
        auto: dict[str, Any] | None = yaml_only
    else:
        auto = (
            next(
                (a for a in yaml_automations if str(a.get("id")) == automation_id),
                None,
            )
            if automation_id
            else None
        )
        if auto is None:
            # Id-less yaml entry — locate by entity_id resolution
            auto = next(
                (
                    a
                    for a in yaml_automations
                    if isinstance(a, dict)
                    and _resolve_yaml_automation_entity_id(hass, a) == entity_id
                ),
                None,
            )
    is_selora = _is_selora(auto) if auto else False

    store: AutomationStore = _get_automation_store(hass)
    record: AutomationRecord | None = (
        await store.get_record(automation_id) if (automation_id and is_selora) else None
    )
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

    yaml_text: str = _yaml.dump(auto, allow_unicode=True, default_flow_style=False) if auto else ""
    risk: RiskAssessment | None = assess_automation_risk(auto) if auto else None

    if state is None:
        status = "not_loaded"
    elif is_selora and auto and _is_pending_automation(auto, record):
        status = "pending"
    else:
        status = "enabled" if state.state == "on" else "disabled"

    if state is not None:
        alias_raw = state.attributes.get("friendly_name") or (auto.get("alias", "") if auto else "")
    else:
        alias_raw = auto.get("alias", "") if auto else ""

    return {
        "automation_id": automation_id,
        "entity_id": entity_id or "",
        "alias": _sanitize(alias_raw),
        "yaml": yaml_text,
        "status": status,
        "selora_managed": is_selora,
        "source": "yaml" if auto else "storage",
        "version_count": len(versions),
        "versions": versions,
        "lineage": lineage,
        "risk_assessment": _sanitize_risk(risk) if risk else None,
    }


# ── Tool: selora_validate_automation ──────────────────────────────────────────


async def _tool_validate_automation(
    hass: HomeAssistant, arguments: dict[str, Any]
) -> dict[str, Any]:
    """Validate + risk-assess YAML without creating anything. Pure read."""
    import yaml as _yaml

    from ..automation_utils import assess_automation_risk, validate_automation_payload
    from ..helpers import CALLER_IS_ADMIN

    yaml_text: str = str(arguments.get("yaml", ""))
    if not yaml_text.strip():
        return {
            "valid": False,
            "errors": ["yaml field is required"],
            "normalized_yaml": None,
            "risk_assessment": None,
        }

    # Parse
    try:
        parsed: Any = await hass.async_add_executor_job(lambda: _yaml.safe_load(yaml_text))
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
    is_valid: bool
    reason: str
    normalized: AutomationDict | None
    is_valid, reason, normalized = validate_automation_payload(parsed, hass)
    if not is_valid or normalized is None:
        return {
            "valid": False,
            "errors": [reason] if reason else ["Validation failed"],
            "normalized_yaml": None,
            "risk_assessment": None,
        }

    blueprint_note: str | None = None
    if (use_blueprint := normalized.get("use_blueprint")) and not CALLER_IS_ADMIN.get():
        # Reading a blueprint is admin-only, as in Home Assistant, and a
        # refusal would name its inputs and selectors. Writing stays admin-only,
        # so create_automation still checks them.
        blueprint_note = "Blueprint inputs are checked for admin callers only."
    elif use_blueprint:
        from ..blueprint_inputs import async_blueprint_error  # noqa: PLC0415

        if error := await async_blueprint_error(
            hass, use_blueprint, str(normalized.get("alias") or "")
        ):
            return {
                "valid": False,
                "errors": [error],
                "normalized_yaml": None,
                "risk_assessment": None,
            }

    normalized_yaml: str = _yaml.dump(normalized, allow_unicode=True, default_flow_style=False)
    risk: RiskAssessment = assess_automation_risk(normalized)

    result: dict[str, Any] = {
        "valid": True,
        "errors": [],
        "normalized_yaml": normalized_yaml,
        "risk_assessment": _sanitize_risk(risk),
    }
    if blueprint_note:
        result["note"] = blueprint_note
    return result


# ── Tool: selora_create_automation ────────────────────────────────────────────


async def _tool_create_automation(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Create an automation from externally-provided YAML, or replace one by id.

    Server-side validation and risk assessment run unconditionally.
    Automations are created disabled by default.

    ``automation_id`` makes this the write path for a refinement: an agent that
    took a revised automation out of ``selora_chat`` has nowhere else to put it,
    and creating it again leaves two automations with one alias both running.

    Any automation in automations.yaml can be replaced, on one of two paths. A
    Selora-managed one goes through the proposal validator and gets a version
    record. Any other is validated by Home Assistant's own automation validator
    and written exactly as given — the proposal validator reshapes what it
    accepts and holds hand-written YAML to rules it need not meet. Both keep
    the automation's enabled state and the risk gate.
    """
    import yaml as _yaml

    from ..automation_utils import (
        assess_automation_risk,
        async_create_automation,
        async_update_automation,
        validate_automation_payload,
    )

    yaml_text: str = str(arguments.get("yaml", ""))
    enabled: bool = bool(arguments.get("enabled", False))
    target_id: str = str(arguments.get("automation_id", "")).strip()
    version_message: str = _sanitize(
        arguments.get("version_message", "Updated via MCP" if target_id else "Created via MCP")
    )

    if not yaml_text.strip():
        return {"error": "yaml field is required"}

    try:
        parsed: Any = await hass.async_add_executor_job(lambda: _yaml.safe_load(yaml_text))
    except _yaml.YAMLError as exc:
        return {"error": f"YAML parse error: {exc}"}

    if not isinstance(parsed, dict):
        return {"error": "YAML must be a mapping"}

    if target_id:
        existing = next(
            (
                a
                for a in await _read_yaml_automations(hass)
                if isinstance(a, dict) and str(a.get("id", "")) == target_id
            ),
            None,
        )
        if existing is None:
            return {
                "error": (
                    f"Automation {_sanitize(target_id)} is not in automations.yaml. Only "
                    "automations saved there (the ones Home Assistant's editor manages) "
                    "can be replaced; one defined in a package or another file has to "
                    "be edited there."
                )
            }
        if not _is_selora(existing):
            return await _replace_user_automation(hass, target_id, parsed)

    is_valid: bool
    reason: str
    normalized: AutomationDict | None
    is_valid, reason, normalized = validate_automation_payload(parsed, hass)
    if not is_valid or normalized is None:
        return {"error": f"Invalid automation: {reason}"}

    risk: RiskAssessment = assess_automation_risk(normalized)

    if target_id:
        # preserve_enabled_state: this is a content revision, not an
        # enable/disable, so the automation's current state is left alone and
        # `enabled` is ignored (documented on the tool). The risk gate inside
        # still forces a newly-elevated automation off for review.
        # The risk gate inside can leave the automation off, and only it knows:
        # an automation whose boot override was already False can still be live
        # after a manual toggle, and the gate leaves that off by skipping the
        # restore rather than by writing anything, so the file cannot be read to
        # infer it. Reporting `updated` alone tells the caller the revision is
        # running while the tool promises a replacement keeps its enabled state.
        update_report: dict[str, Any] = {}
        if not await async_update_automation(
            hass,
            target_id,
            dict(normalized),
            version_message=version_message,
            report=update_report,
        ):
            return {"error": f"Failed to update automation {_sanitize(target_id)}"}
        response: dict[str, Any] = {
            "automation_id": target_id,
            "status": "updated",
            "risk_assessment": _sanitize_risk(risk),
        }
        if update_report.get("forced_disabled"):
            response["forced_disabled"] = True
            response["note"] = (
                "Automation left DISABLED because the replacement uses elevated-risk "
                "primitives (shell_command, python_script, webhook, etc.). Review "
                "it and enable manually if intended."
            )
        return attach_previous(response, update_report.get("previous"), what="old automation")

    # A deleted automation's returned copy carries its id: passed back, it is
    # made again under that id with its history while the history is kept.
    restore_id = parsed.get("id")
    create_result = await async_create_automation(
        hass,
        normalized,
        version_message=version_message,
        enabled=enabled,
        restore_id=restore_id if isinstance(restore_id, str) and restore_id else None,
    )
    if not create_result.get("success"):
        return {"error": "Failed to write automation to automations.yaml"}

    automation_id: str = create_result.get("automation_id") or ""
    forced_disabled: bool = bool(create_result.get("forced_disabled"))

    response: dict[str, Any] = {
        "automation_id": automation_id,
        "status": "created",
        "risk_assessment": _sanitize_risk(risk),
    }
    if create_result.get("history_restored"):
        response["history_restored"] = True
    if forced_disabled:
        response["forced_disabled"] = True
        response["note"] = (
            "Automation created disabled because it uses elevated-risk primitives "
            "(shell_command, python_script, webhook trigger, etc.). Review and "
            "enable manually if intended."
        )
    return response


async def _replace_user_automation(
    hass: HomeAssistant, target_id: str, config: dict[str, Any]
) -> dict[str, Any]:
    """Replace an automation Selora did not create, exactly as given.

    Validated by Home Assistant rather than the proposal validator, so the
    user's own YAML is neither reshaped nor held to Selora's rules. Its enabled
    state is kept, and the risk gate still turns off an edit that newly adds
    elevated-risk primitives.
    """
    from ..automation_utils import assess_automation_risk, async_update_automation

    config = {k: v for k, v in config.items() if k != "id"}
    risk: RiskAssessment = assess_automation_risk(config)
    report: dict[str, Any] = {}
    if not await async_update_automation(
        hass,
        target_id,
        config,
        report=report,
        validate_with="home_assistant",
    ):
        return {
            "error": _sanitize(
                report.get("error") or f"Failed to update automation {target_id}", limit=500
            )
        }
    response: dict[str, Any] = {
        "automation_id": target_id,
        "status": "updated",
        "risk_assessment": _sanitize_risk(risk),
    }
    if report.get("forced_disabled"):
        response["forced_disabled"] = True
        response["note"] = (
            "Automation left DISABLED because the replacement adds elevated-risk "
            "primitives (shell_command, python_script, webhook, etc.). Review it "
            "and enable manually if intended."
        )
    return attach_previous(response, report.get("previous"), what="old automation")


# ── Tool: selora_accept_automation ────────────────────────────────────────────


async def _tool_accept_automation(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Enable or disable any HA automation.

    For yaml-managed automations, persists ``initial_state`` so the change
    survives restarts (and creates a version record for Selora-managed ones).
    For storage-managed automations, calls ``automation.turn_on/off`` so the
    change applies at runtime — HA persists storage-managed enabled state
    automatically.
    """
    from ..automation_utils import async_toggle_automation, async_update_automation

    automation_id: str = str(arguments.get("automation_id", "")).strip()
    entity_id_arg: str = str(arguments.get("entity_id", "")).strip()
    enabled: bool = bool(arguments.get("enabled", False))

    if not automation_id and not entity_id_arg:
        return {"error": "automation_id or entity_id is required"}

    state, automation_id, entity_id = _resolve_automation(
        hass, automation_id=automation_id, entity_id=entity_id_arg
    )
    if state is None:
        ref = entity_id_arg or automation_id
        return {"error": f"Automation {_sanitize(ref)} not found"}

    yaml_automations: list[dict[str, Any]] = await _read_yaml_automations(hass)
    auto: dict[str, Any] | None = (
        next(
            (a for a in yaml_automations if str(a.get("id")) == automation_id),
            None,
        )
        if automation_id
        else None
    )
    if auto is None:
        auto = next(
            (
                a
                for a in yaml_automations
                if isinstance(a, dict) and _resolve_yaml_automation_entity_id(hass, a) == entity_id
            ),
            None,
        )

    # Yaml entries with an ``id``:
    #  - Selora-managed → ``async_update_automation`` (writes a version record;
    #    proposal-shape validation is safe because Selora authors the YAML).
    #  - Non-Selora → ``async_toggle_automation`` (just flips ``initial_state``
    #    and calls the service; avoids re-validating user-authored YAML that
    #    may use constructs outside Selora's proposal validator).
    # Id-less yaml entries and storage-managed automations use the service
    # call directly. Yaml-without-id can't persist ``initial_state``.
    if auto is not None and isinstance(auto.get("id"), str) and auto["id"]:
        yaml_id = str(auto["id"])
        if _is_selora(auto):
            updated: dict[str, Any] = dict(auto)
            updated["initial_state"] = enabled
            success: bool = await async_update_automation(
                hass,
                yaml_id,
                updated,
                version_message="Accepted via MCP",
                # This is an explicit enable/disable; honor initial_state above
                # instead of preserving the pre-command live state.
                preserve_enabled_state=False,
            )
        else:
            success = await async_toggle_automation(hass, yaml_id, entity_id, enabled)
        if not success:
            return {"error": "Failed to update automation"}
        return {
            "automation_id": yaml_id,
            "entity_id": entity_id,
            "status": "enabled" if enabled else "disabled",
            "persisted": True,
        }

    service = "turn_on" if enabled else "turn_off"
    try:
        await hass.services.async_call(
            "automation", service, {"entity_id": entity_id}, blocking=True
        )
    except Exception as exc:  # noqa: BLE001
        return {"error": f"Failed to {service}: {exc}"}

    response: dict[str, Any] = {
        "automation_id": automation_id,
        "entity_id": entity_id,
        "status": "enabled" if enabled else "disabled",
        "persisted": auto is None,  # storage-managed persists; yaml-without-id doesn't
    }
    if auto is not None:
        response["warning"] = (
            "Yaml automation has no 'id' field; runtime state changed but "
            "initial_state cannot be persisted. Add an 'id' to the entry in "
            "automations.yaml to persist enable/disable across HA restarts."
        )
    return response


# ── Tool: selora_delete_automation ────────────────────────────────────────────


async def _delete_idless_automation_by_alias(
    hass: HomeAssistant, expected_alias: str
) -> dict[str, Any]:
    """Atomically delete the id-less yaml automation whose alias equals
    *expected_alias*, holding ``AUTOMATIONS_YAML_LOCK`` across the find,
    ambiguity check, and removal.

    Used by the chat delete-confirmation flow, where the identity fingerprint
    (the alias) was captured when the card was shown. Enforcing it here — under
    the lock, re-reading the file inside the lock — closes the TOCTOU window
    between a separate identity check and the delete: the entry removed is
    always the one matching the confirmed alias, never whatever a since-changed
    entity_id now resolves to. Refuses on 0 matches ("changed since shown") or
    >1 (ambiguous), so the wrong entry is never removed.
    """
    from ..automation_utils import (  # noqa: PLC0415
        AUTOMATIONS_YAML_LOCK,
        _read_automations_yaml,
        _write_automations_yaml,
    )

    target = expected_alias.strip()
    automations_path = Path(hass.config.config_dir) / "automations.yaml"

    def _is_idless_match(entry: Any) -> bool:
        return (
            isinstance(entry, dict)
            and isinstance(entry.get("alias"), str)
            and entry.get("alias", "").strip() == target
            and not (isinstance(entry.get("id"), str) and entry["id"])
        )

    async with AUTOMATIONS_YAML_LOCK:
        existing = await hass.async_add_executor_job(_read_automations_yaml, automations_path)
        matches = [a for a in existing if _is_idless_match(a)]
        if not matches:
            return {
                "error": (
                    f"Automation “{_sanitize(target)}” changed since it was shown; not deleted."
                )
            }
        if len(matches) > 1:
            return {
                "error": (
                    f"Multiple id-less entries share the alias {_sanitize(target)!r}; "
                    "add an 'id' to the target entry to enable safe deletion."
                )
            }
        previous = list(existing)
        remaining = [a for a in existing if not _is_idless_match(a)]
        await hass.async_add_executor_job(_write_automations_yaml, automations_path, remaining)
        try:
            await hass.services.async_call("automation", "reload", blocking=True)
        except Exception as exc:  # noqa: BLE001 — restore on reload failure
            await hass.async_add_executor_job(_write_automations_yaml, automations_path, previous)
            return {"error": f"Automation reload failed: {exc}"}

    return attach_previous({"status": "deleted"}, matches[0], what="deleted automation")


async def _tool_delete_automation(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Delete any yaml-managed HA automation.

    Storage-managed (UI/integration) automations cannot be removed through this
    tool — those must be deleted from the HA UI or integration that owns them.
    """
    from ..automation_utils import async_delete_automation

    automation_id: str = str(arguments.get("automation_id", "")).strip()
    entity_id_arg: str = str(arguments.get("entity_id", "")).strip()

    # Delete-confirmation path for id-less entries: an ``expected_alias`` was
    # captured when the card was shown. Enforce it atomically and ignore the
    # (mutable) entity_id entirely, so an entity remap between render and click
    # can't redirect the delete to a different entry.
    expected_alias: str = str(arguments.get("expected_alias", "")).strip()
    if expected_alias:
        return await _delete_idless_automation_by_alias(hass, expected_alias)

    if not automation_id and not entity_id_arg:
        return {"error": "automation_id or entity_id is required"}

    state, resolved_id, resolved_entity = _resolve_automation(
        hass, automation_id=automation_id, entity_id=entity_id_arg
    )
    # An unloaded YAML entry resolves to empty ids; the id asked for is then
    # the only handle on it (as in ``_preview_delete_automation``).
    automation_id = resolved_id or automation_id
    entity_id = resolved_entity or entity_id_arg

    yaml_automations: list[dict[str, Any]] = await _read_yaml_automations(hass)
    # Yaml-only fallback so broken/un-loaded yaml entries can still be cleaned
    # up by id — the previous implementation supported this and removing it
    # would block recovery from a bad manual edit.
    if state is None:
        if not automation_id:
            return {"error": f"Automation {_sanitize(entity_id_arg)} not found"}
        yaml_only = next(
            (a for a in yaml_automations if str(a.get("id")) == automation_id),
            None,
        )
        if yaml_only is None:
            return {"error": f"Automation {_sanitize(automation_id)} not found"}
        auto: dict[str, Any] | None = yaml_only
    else:
        auto = (
            next(
                (a for a in yaml_automations if str(a.get("id")) == automation_id),
                None,
            )
            if automation_id
            else None
        )
        if auto is None:
            auto = next(
                (
                    a
                    for a in yaml_automations
                    if isinstance(a, dict)
                    and _resolve_yaml_automation_entity_id(hass, a) == entity_id
                ),
                None,
            )
        if auto is None:
            return {
                "error": (
                    f"Automation {_sanitize(entity_id)} is not yaml-managed; "
                    "delete it from the Home Assistant UI instead."
                )
            }

    yaml_id = auto.get("id")
    if isinstance(yaml_id, str) and yaml_id:
        report: dict[str, Any] = {}
        success: bool = await async_delete_automation(
            hass, yaml_id, report=report, keep_history=True
        )
        if not success:
            return {"error": "Failed to delete automation"}
        return attach_previous(
            {"automation_id": yaml_id, "entity_id": entity_id, "status": "deleted"},
            report.get("previous"),
            what="deleted automation",
        )

    # Id-less yaml entry — delete by alias match. Refuse if the alias is missing
    # or duplicated so we never remove the wrong entry.
    target_alias = auto.get("alias")
    if not isinstance(target_alias, str) or not target_alias.strip():
        return {
            "error": (
                f"Automation {_sanitize(entity_id)} has no 'id' or 'alias' field "
                "in automations.yaml; cannot identify the entry safely."
            )
        }
    duplicates = sum(
        1
        for a in yaml_automations
        if isinstance(a, dict)
        and isinstance(a.get("alias"), str)
        and a.get("alias", "").strip() == target_alias.strip()
    )
    if duplicates > 1:
        return {
            "error": (
                f"Automation {_sanitize(entity_id)} has no 'id' field and "
                f"multiple entries share the alias {_sanitize(target_alias)!r}; "
                "add an 'id' to the target entry to enable safe deletion."
            )
        }

    from ..automation_utils import (  # noqa: PLC0415
        AUTOMATIONS_YAML_LOCK,
        _read_automations_yaml,
        _write_automations_yaml,
    )

    automations_path = Path(hass.config.config_dir) / "automations.yaml"
    async with AUTOMATIONS_YAML_LOCK:
        existing = await hass.async_add_executor_job(_read_automations_yaml, automations_path)
        previous = list(existing)
        remaining = [
            a
            for a in existing
            if not (
                isinstance(a, dict)
                and isinstance(a.get("alias"), str)
                and a.get("alias", "").strip() == target_alias.strip()
                and not (isinstance(a.get("id"), str) and a["id"])
            )
        ]
        removed = [a for a in existing if not any(a is kept for kept in remaining)]
        if len(remaining) == len(existing):
            return {"error": f"Automation {_sanitize(entity_id)} not found in automations.yaml"}
        await hass.async_add_executor_job(_write_automations_yaml, automations_path, remaining)
        try:
            await hass.services.async_call("automation", "reload", blocking=True)
        except Exception as exc:  # noqa: BLE001 — restore on reload failure
            await hass.async_add_executor_job(_write_automations_yaml, automations_path, previous)
            return {"error": f"Automation reload failed: {exc}"}

    return attach_previous(
        {"entity_id": entity_id, "status": "deleted"},
        removed[0] if len(removed) == 1 else None,
        what="deleted automation",
    )


async def _preview_delete_automation(
    hass: HomeAssistant, arguments: dict[str, Any]
) -> dict[str, Any]:
    """Resolve a delete_automation target WITHOUT deleting it.

    Read-only counterpart of :func:`_tool_delete_automation` used by the
    chat ``delete_automation`` tool: it identifies the yaml-managed target
    and returns a ``{kind, target_id, entity_id, label}`` descriptor for
    the confirmation card. The authoritative safeguards (yaml-managed only,
    alias disambiguation, reload rollback) still run at confirm time when
    ``_tool_delete_automation`` executes — this preview just needs enough to
    show the user what they're about to delete, and to refuse early when
    there's nothing deletable.
    """
    automation_id: str = str(arguments.get("automation_id", "")).strip()
    entity_id_arg: str = str(arguments.get("entity_id", "")).strip()

    if not automation_id and not entity_id_arg:
        return {"error": "automation_id or entity_id is required"}

    state, resolved_id, resolved_entity = _resolve_automation(
        hass, automation_id=automation_id, entity_id=entity_id_arg
    )
    # Preserve the caller-provided identifiers when the automation isn't
    # currently loaded: ``_resolve_automation`` returns empty ids for an
    # unloaded YAML entry, but the delete backend supports removing such an
    # entry by id — so the preview must keep the id to find it below.
    automation_id = resolved_id or automation_id
    entity_id = resolved_entity or entity_id_arg

    yaml_automations: list[dict[str, Any]] = await _read_yaml_automations(hass)
    entry: dict[str, Any] | None = None
    if automation_id:
        entry = next(
            (a for a in yaml_automations if str(a.get("id")) == automation_id),
            None,
        )
    if entry is None and entity_id:
        entry = next(
            (
                a
                for a in yaml_automations
                if isinstance(a, dict) and _resolve_yaml_automation_entity_id(hass, a) == entity_id
            ),
            None,
        )

    if entry is None:
        if state is None:
            ref = entity_id_arg or automation_id
            return {"error": f"Automation {_sanitize(ref)} not found"}
        return {
            "error": (
                f"Automation {_sanitize(entity_id)} is not yaml-managed; "
                "delete it from the Home Assistant UI instead."
            )
        }

    # Derive the stable id from the resolved entry itself, never from the
    # caller's input. If both identifiers were supplied and entity_id resolved
    # to an id-less entry, the caller's automation_id is stale — keeping it as
    # target_id would make the confirm delete by that id and return not found.
    # An empty target_id routes the confirm through the entity + alias
    # fingerprint path instead (mirrors the scene preview's stale-id handling).
    entry_id_raw = entry.get("id")
    entry_id = entry_id_raw if isinstance(entry_id_raw, str) and entry_id_raw else ""
    entry_alias = entry.get("alias") if isinstance(entry.get("alias"), str) else None
    label = (
        entry_alias
        or (state.attributes.get("friendly_name") if state is not None else None)
        or entity_id
        or entry_id
    )
    return {
        "requires_approval": True,
        "delete": {
            "kind": "automation",
            "target_id": entry_id,
            "entity_id": entity_id,
            # Immutable fingerprint for the id-less confirm path: entity_id is
            # mutable, so the confirm handler re-checks this alias before the
            # by-entity fallback delete to be sure it's still the same entry.
            "alias": entry_alias or "",
            "label": str(label),
        },
    }


# ── Tool: selora_trigger_automation ───────────────────────────────────────────


async def _tool_trigger_automation(
    hass: HomeAssistant, arguments: dict[str, Any]
) -> dict[str, Any]:
    """Trigger any HA automation immediately via the ``automation.trigger`` service.

    Accepts either ``automation_id`` or ``entity_id``. By default the automation's
    conditions are skipped (matching the HA service default); pass
    ``skip_condition=False`` to honour them.
    """
    automation_id: str = str(arguments.get("automation_id", "")).strip()
    entity_id_arg: str = str(arguments.get("entity_id", "")).strip()
    skip_condition: bool = bool(arguments.get("skip_condition", True))

    if not automation_id and not entity_id_arg:
        return {"error": "automation_id or entity_id is required"}

    state, automation_id, entity_id = _resolve_automation(
        hass, automation_id=automation_id, entity_id=entity_id_arg
    )
    if state is None:
        ref = entity_id_arg or automation_id
        return {"error": f"Automation {_sanitize(ref)} not found"}

    try:
        await hass.services.async_call(
            "automation",
            "trigger",
            {"entity_id": entity_id, "skip_condition": skip_condition},
            blocking=True,
        )
    except Exception as exc:  # noqa: BLE001
        _LOGGER.error("Failed to trigger automation %s: %s", entity_id, exc)
        return {"error": f"Trigger failed: {exc}"}

    return {"automation_id": automation_id, "entity_id": entity_id, "status": "triggered"}
