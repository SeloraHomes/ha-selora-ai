"""Selora AI websocket handlers: Insights (health signals + advisor).

Powers the panel's Insights tab. Read-only listing plus per-insight status
updates (dismiss / acknowledge / resolve) and an on-demand rescan.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components import websocket_api
from homeassistant.components.websocket_api import decorators
from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
import voluptuous as vol

from .. import _require_admin
from ..const import DOMAIN

_LOGGER = logging.getLogger(__name__)

_INSIGHT_STATUSES = ("new", "acknowledged", "resolved", "dismissed")
_SEVERITY_RANK = {"critical": 0, "warning": 1, "info": 2}
# Offline == literal ``unavailable``; ``unknown`` is a valid no-value state.
_UNAVAILABLE_STATES = frozenset({"unavailable"})


def _device_fully_down(hass: HomeAssistant, ent_reg: er.EntityRegistry, device_id: str) -> bool:
    """True when every (enabled, stateful) entity of the device is unavailable.

    Lets the UI collapse a wholly-offline device to one row instead of listing
    all its entities — the user's point: one dead device is one problem. A
    single-entity device counts as fully down when its one entity is
    unavailable (``total >= 1``), matching the monitor's ``_device_unreachable``
    gate — otherwise common single-entity devices never get an offline fix card.
    """
    entries = er.async_entries_for_device(ent_reg, device_id, include_disabled_entities=False)
    total = 0
    unavailable = 0
    for entry in entries:
        state = hass.states.get(entry.entity_id)
        if state is None:
            continue
        total += 1
        if state.state in _UNAVAILABLE_STATES:
            unavailable += 1
    return total >= 1 and unavailable == total


def _get_insights_bucket(hass: HomeAssistant) -> dict[str, Any] | None:
    """Return the first entry bucket that has the Insights subsystem wired."""
    for key, val in hass.data.get(DOMAIN, {}).items():
        if key.startswith("_") or not isinstance(val, dict):
            continue
        if "insights_engine" in val:
            return val
    return None


async def _rescan_health(bucket: dict[str, Any] | None) -> None:
    """Reconcile Layer-1 health signals so an audit built right after reflects
    live state, not a cache up to a scan-interval old (recovered devices,
    changed battery levels, first open before the initial scan). Best-effort —
    a scan failure falls back to the cached signals rather than blocking.
    """
    monitor = bucket.get("health_monitor") if bucket else None
    if monitor is not None:
        try:
            await monitor.async_request_scan()
        except Exception:  # noqa: BLE001 — a scan failure must not block the audit
            _LOGGER.exception("Pre-audit health rescan failed; using cached signals")


def _group_signals(hass: HomeAssistant, signals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse per-entity signals into per-device groups for the Troubleshooting
    view — so one flaky device is one card (with clickable entities), not 5-8
    cryptic rows. Integration signals group by domain.
    """
    ent_reg = er.async_get(hass)
    dev_reg = dr.async_get(hass)
    area_reg = ar.async_get(hass)

    def area_name(area_id: str | None) -> str:
        if not area_id:
            return ""
        area = area_reg.async_get_area(area_id)
        return area.name if area else ""

    groups: dict[str, dict[str, Any]] = {}
    for sig in signals:
        target = sig.get("target", "")
        target_kind = sig.get("target_kind", "entity")
        device_id: str | None = None
        friendly = target

        if target_kind == "entity":
            ent = ent_reg.async_get(target)
            state = hass.states.get(target)
            friendly = (
                (state.attributes.get("friendly_name") if state else None)
                or (ent.name or ent.original_name if ent else None)
                or target
            )
            device_id = ent.device_id if ent else None
            if device_id:
                dev = dev_reg.async_get(device_id)
                group_key = f"device:{device_id}"
                group_name = (dev.name_by_user or dev.name if dev else None) or friendly
                area_id = (dev.area_id if dev else None) or (ent.area_id if ent else None)
            else:
                group_key = f"entity:{target}"
                group_name = friendly
                area_id = ent.area_id if ent else None
        else:
            group_key = f"integration:{target}"
            group_name = target
            area_id = None

        group = groups.setdefault(
            group_key,
            {
                "group_id": group_key,
                "name": group_name,
                "target_kind": target_kind,
                "device_id": device_id,
                "area": area_name(area_id),
                "severity": "info",
                "items": [],
            },
        )
        group["items"].append(
            {
                "kind": sig.get("kind"),
                "severity": sig.get("severity"),
                "entity_id": target if target_kind == "entity" else None,
                "friendly_name": friendly,
                "evidence": sig.get("evidence", {}),
            }
        )
        if _SEVERITY_RANK.get(sig.get("severity"), 3) < _SEVERITY_RANK.get(group["severity"], 3):
            group["severity"] = sig.get("severity")

    for group in groups.values():
        group["fully_down"] = bool(
            group["device_id"] and _device_fully_down(hass, ent_reg, group["device_id"])
        )

    out = list(groups.values())
    out.sort(key=lambda g: (_SEVERITY_RANK.get(g["severity"], 3), -len(g["items"])))
    return out


# Actionable signal kinds → a short imperative for the "To fix" section, plus
# whether the whole-device-offline case should be promoted as a fix.
def _build_fixes(groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Device-collapsed, deterministic actionable items for the 'To fix' section.

    Built from the same per-device signal groups the Troubleshooting view uses,
    so a low battery or a dead device surfaces once as a clear action — not
    buried per-entity in the troubleshooting dump, and not dependent on the LLM.
    """
    fixes: list[dict[str, Any]] = []
    for group in groups:
        name = group["name"]
        area = group["area"]
        kinds = {item.get("kind") for item in group["items"]}
        entities = [it["entity_id"] for it in group["items"] if it.get("entity_id")][:6]
        area_suffix = f" ({area})" if area else ""

        if "battery_low" in kinds:
            levels = [
                it["evidence"].get("battery_level")
                for it in group["items"]
                if it.get("kind") == "battery_low"
            ]
            levels = [lvl for lvl in levels if isinstance(lvl, (int, float))]
            level_txt = f" ({int(min(levels))}%)" if levels else ""
            fixes.append(
                {
                    "fix_id": f"battery:{group['group_id']}",
                    "kind": "battery_low",
                    "severity": group["severity"],
                    "title": f"Replace battery: {name}{level_txt}",
                    "detail": f"{name}{area_suffix} battery is low — replace or recharge it "
                    "soon to avoid a gap in coverage.",
                    "entities": entities,
                    "device_id": group.get("device_id"),
                }
            )
        elif group.get("fully_down"):
            fixes.append(
                {
                    "fix_id": f"offline:{group['group_id']}",
                    "kind": "unavailable",
                    "severity": group["severity"],
                    "title": f"{name} is offline",
                    "detail": f"Every entity on {name}{area_suffix} is unavailable — check its "
                    "power, battery, or network/hub connection.",
                    "entities": entities,
                    "device_id": group.get("device_id"),
                }
            )
        elif "integration_error" in kinds:
            fixes.append(
                {
                    "fix_id": f"integration:{group['group_id']}",
                    "kind": "integration_error",
                    "severity": group["severity"],
                    "title": f"{name} integration needs attention",
                    "detail": f"The {name} integration reported an error — check its "
                    "configuration or credentials in Settings → Devices & Services.",
                    "entities": entities,
                    "device_id": group.get("device_id"),
                    # Deep-link to the integration's page (an integration has no
                    # entity to click). ``name`` is the domain for integration
                    # groups (group_key "integration:<domain>").
                    "link": f"/config/integrations/integration/{name}",
                    "link_label": "Open in Settings",
                }
            )

    fixes.sort(key=lambda f: _SEVERITY_RANK.get(f["severity"], 3))
    return fixes


@websocket_api.async_response
@decorators.websocket_command(
    {
        vol.Required("type"): "selora_ai/insights/list",
    }
)
async def _handle_list_insights(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Return current insights + active health signals + export status."""
    if not _require_admin(connection, msg):
        return

    bucket = _get_insights_bucket(hass)
    if bucket is None:
        connection.send_result(
            msg["id"],
            {"enabled": False, "insights": [], "signals": [], "last_scan": None},
        )
        return

    engine = bucket["insights_engine"]
    store = bucket["health_store"]
    exporter = bucket.get("insights_exporter")

    insights = await engine.async_get_insights()
    signals = await store.get_active_signals()
    last_scan = await store.get_last_scan()

    export_enabled = False
    if exporter is not None:
        export_enabled = await hass.async_add_executor_job(exporter.marker_path.exists)

    groups = _group_signals(hass, signals)
    connection.send_result(
        msg["id"],
        {
            "enabled": True,
            "insights": insights,
            "signals": signals,
            "fixes": _build_fixes(groups),
            "troubleshooting": groups,
            "last_scan": last_scan,
            "export_enabled": export_enabled,
        },
    )


@websocket_api.async_response
@decorators.websocket_command(
    {
        vol.Required("type"): "selora_ai/insights/audit",
    }
)
async def _handle_get_audit(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Run the deterministic checks and return the result (checks + score).

    The checks are cheap and deterministic, so we run fresh on each load — the
    device-health rows and score reflect the live state, not a stale cache.
    """
    if not _require_admin(connection, msg):
        return
    bucket = _get_insights_bucket(hass)
    runner = bucket.get("audit_runner") if bucket else None
    if runner is None:
        connection.send_result(msg["id"], {"status": "unavailable"})
        return
    # Reconcile signals first so the live page reflects current state (a
    # just-recovered device, a changed battery, or the very first open before
    # the initial scan) rather than a cache up to a scan-interval old.
    await _rescan_health(bucket)
    # Tracked, like the rerun: this awaits work (automations.yaml read, the
    # scan above) so a reload mid-run must be able to cancel/drain it via
    # async_stop — otherwise the old runner could persist through a stale
    # HealthStore and clobber the replacement entry's data.
    audit = await runner.async_run_tracked()
    connection.send_result(msg["id"], audit or {"status": "pending"})


@websocket_api.async_response
@decorators.websocket_command(
    {
        vol.Required("type"): "selora_ai/insights/audit_rerun",
    }
)
async def _handle_rerun_audit(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Force a fresh home audit and return the new result."""
    if not _require_admin(connection, msg):
        return
    bucket = _get_insights_bucket(hass)
    runner = bucket.get("audit_runner") if bucket else None
    if runner is None:
        connection.send_error(msg["id"], "no_audit", "Audit runner not available")
        return
    await _rescan_health(bucket)
    # Tracked so a reload mid-rerun cancels/drains it (async_stop) instead of
    # letting the old runner persist its stale store over the new entry's.
    audit = await runner.async_run_tracked(force=True)
    connection.send_result(msg["id"], audit)


@websocket_api.async_response
@decorators.websocket_command(
    {
        vol.Required("type"): "selora_ai/insights/set_status",
        vol.Required("insight_id"): cv.string,
        vol.Required("status"): vol.In(_INSIGHT_STATUSES),
    }
)
async def _handle_set_insight_status(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Persist a user action on an insight (dismiss / acknowledge / resolve)."""
    if not _require_admin(connection, msg):
        return

    bucket = _get_insights_bucket(hass)
    if bucket is None:
        connection.send_error(msg["id"], "no_insights", "Insights not enabled")
        return

    await bucket["insights_engine"].set_insight_status(msg["insight_id"], msg["status"])
    connection.send_result(msg["id"], {"success": True})


@websocket_api.async_response
@decorators.websocket_command(
    {
        vol.Required("type"): "selora_ai/insights/rescan",
    }
)
async def _handle_rescan(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Run the health detectors now and return the refreshed insights."""
    if not _require_admin(connection, msg):
        return

    bucket = _get_insights_bucket(hass)
    if bucket is None:
        connection.send_error(msg["id"], "no_insights", "Insights not enabled")
        return

    monitor = bucket.get("health_monitor")
    if monitor is not None:
        # Tracked so a reload mid-rescan cancels/drains it (async_stop) instead
        # of letting the old scan finish and clobber the new entry's store.
        await monitor.async_request_scan()

    engine = bucket["insights_engine"]
    store = bucket["health_store"]
    connection.send_result(
        msg["id"],
        {
            "insights": await engine.async_get_insights(),
            "signals": await store.get_active_signals(),
            "last_scan": await store.get_last_scan(),
        },
    )


def async_register(hass: HomeAssistant) -> None:
    """Register the Insights websocket commands."""
    websocket_api.async_register_command(hass, _handle_list_insights)
    websocket_api.async_register_command(hass, _handle_set_insight_status)
    websocket_api.async_register_command(hass, _handle_rescan)
    websocket_api.async_register_command(hass, _handle_get_audit)
    websocket_api.async_register_command(hass, _handle_rerun_audit)
