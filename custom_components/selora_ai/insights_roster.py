"""Home roster builder — the complete "what's running, what's not" inventory.

Walks HA's registries + state machine to produce a full device-plane roster
(integrations / devices / entities / automations / scripts / scenes) with
current state and availability. Shipped in the export envelope (schema v2) so
the Selora OS host and Connect can render the whole home, not just the
exception signals.

This is the user's own home going to their own account (keyed by
installation_id), so it carries identities + state — distinct from the
anonymous, counts-only PostHog telemetry. Attribute exposure stays limited to
what the roster fields need (name/state/availability/area), never the raw
attribute bag.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
import logging
from typing import TYPE_CHECKING
from urllib.parse import urlsplit, urlunsplit

from homeassistant.core import HomeAssistant
from homeassistant.helpers import (
    area_registry as ar,
)
from homeassistant.helpers import (
    device_registry as dr,
)
from homeassistant.helpers import (
    entity_registry as er,
)
from homeassistant.helpers import (
    issue_registry as ir,
)

from .const import AUTOMATION_ID_PREFIX, INSIGHTS_ROSTER_MAX_ENTITIES

if TYPE_CHECKING:
    from .types import (
        HomeRoster,
        RosterAutomation,
        RosterDevice,
        RosterEntity,
        RosterIntegration,
        RosterScene,
        RosterScript,
    )

_LOGGER = logging.getLogger(__name__)

# Only the literal ``unavailable`` state counts as offline. ``unknown`` is a
# valid no-value state (TTS/notify services, sun-derived sensors, sensors
# awaiting a first reading) — reporting it as unavailable is a false positive.
_UNAVAILABLE_STATES = frozenset({"unavailable"})
_SELORA_ALIAS_PREFIX = "[Selora AI]"


def _available(state: str) -> bool:
    return state.casefold() not in _UNAVAILABLE_STATES


def _strip_url_credentials(url: str) -> str:
    """Remove embedded userinfo (``user:pass@``) from a URL before export.

    Some integrations set ``configuration_url`` to a device admin page carrying
    basic-auth creds (``http://admin:token@192.168.1.5/``); those must not leave
    the box. Preserves scheme/host/port (incl. IPv6) and path; returns the input
    unchanged when there's nothing to strip or it can't be parsed.
    """
    if not url:
        return url
    try:
        parts = urlsplit(url)
    except ValueError:
        return url
    if "@" not in parts.netloc:
        return url
    netloc = parts.netloc.rsplit("@", 1)[1]  # keep only the host[:port] part
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def _iso_or_none(value: object) -> str | None:
    """Normalize a ``last_triggered`` attribute to an ISO-8601 string.

    HA supplies this as a ``datetime``; ``json.dumps(default=str)`` would
    render it space-separated ("2026-01-01 06:00:00+00:00"), violating the
    envelope schema's ``date-time`` format. Emit ``.isoformat()`` (with the
    ``T`` separator) instead. Already-string values pass through; anything
    else becomes None.
    """
    if isinstance(value, datetime):
        return value.isoformat()
    return value if isinstance(value, str) else None


def build_home_roster(
    hass: HomeAssistant,
    custom_domains: set[str] | None = None,
    integration_names: dict[str, str] | None = None,
    integration_urls: dict[str, str] | None = None,
) -> HomeRoster:
    """Build the full home roster. Must run on the event loop (registry reads).

    ``custom_domains`` (set of custom-component domains), ``integration_names``
    (domain -> manifest name) and ``integration_urls`` (domain -> manifest
    documentation URL) are resolved by the async caller and threaded in because
    this builder is synchronous — see ``insights_export.publish``. Absent →
    ``custom`` defaults False, ``name`` falls back to the config-entry title,
    and ``url`` defaults to "".
    """
    custom_domains = custom_domains or set()
    integration_names = integration_names or {}
    integration_urls = integration_urls or {}
    ent_reg = er.async_get(hass)
    dev_reg = dr.async_get(hass)
    area_reg = ar.async_get(hass)
    issue_reg = ir.async_get(hass)

    def area_name(area_id: str | None) -> str:
        if not area_id:
            return ""
        area = area_reg.async_get_area(area_id)
        return area.name if area else ""

    # entry_id -> (domain, title, state) for integration rows + device mapping.
    entry_meta: dict[str, tuple[str, str, str]] = {}
    for entry in hass.config_entries.async_entries():
        entry_meta[entry.entry_id] = (
            entry.domain,
            entry.title,
            entry.state.name.lower(),
        )
    # Attribute a repair to the integration it AFFECTS, not the one that raised
    # it: HA stores the affected domain in ``issue_domain`` and the creator in
    # ``domain``. Skip issues the user dismissed (active but dismissed_version
    # set) so an ignored repair isn't exported as an active problem — matches
    # the health monitor's handling.
    issue_domains = {
        (i.issue_domain or i.domain)
        for i in issue_reg.issues.values()
        if i.active and i.dismissed_version is None
    }

    # ── Entities (also feeds per-device + per-integration rollups) ──────
    entities: list[RosterEntity] = []
    ent_count_by_entry: dict[str, int] = defaultdict(int)
    ent_count_by_device: dict[str, int] = defaultdict(int)
    unavail_by_device: dict[str, int] = defaultdict(int)
    disabled_by_device: dict[str, int] = defaultdict(int)
    unavailable_total = 0  # enabled, visible entities with no usable state
    disabled_total = 0
    truncated = False

    for ent in ent_reg.entities.values():
        if ent.config_entry_id:
            ent_count_by_entry[ent.config_entry_id] += 1
        if ent.device_id:
            ent_count_by_device[ent.device_id] += 1

        disabled = ent.disabled_by is not None
        hidden = ent.hidden_by is not None
        state_obj = hass.states.get(ent.entity_id)
        if disabled:
            state_str = "disabled"
            available = False
        elif state_obj is None:
            state_str = "unavailable"
            available = False
        else:
            state_str = state_obj.state
            available = _available(state_str)

        # Device-health rollups. "unavailable" means genuinely broken — an
        # ENABLED, visible entity with no usable state. Disabled entities are
        # intentionally off (not broken; counted separately as disabled), and
        # hidden ones aren't user-facing. Without this split a device whose
        # entities are disabled on purpose (e.g. NWS: 12 of 13 disabled) would
        # read as "12 unavailable" / unhealthy when it's actually fine.
        if disabled:
            disabled_total += 1
            if ent.device_id:
                disabled_by_device[ent.device_id] += 1
        elif not available and not hidden:
            unavailable_total += 1
            if ent.device_id:
                unavail_by_device[ent.device_id] += 1

        area = ent.area_id or (
            dev_reg.async_get(ent.device_id).area_id
            if ent.device_id and dev_reg.async_get(ent.device_id)
            else None
        )
        friendly = (
            (state_obj.attributes.get("friendly_name") if state_obj else None)
            or ent.name
            or ent.original_name
            or ent.entity_id
        )
        if len(entities) < INSIGHTS_ROSTER_MAX_ENTITIES:
            entities.append(
                {
                    "entity_id": ent.entity_id,
                    "name": friendly,
                    "domain": ent.domain,
                    "device_class": ent.device_class or ent.original_device_class or "",
                    "area": area_name(area),
                    "state": state_str,
                    "available": available,
                    "last_changed": (state_obj.last_changed.isoformat() if state_obj else None),
                    "disabled": disabled,
                    "hidden": hidden,
                    "device_id": ent.device_id,
                }
            )
        else:
            truncated = True

    # State-only entities: HA entities without a unique_id live in the state
    # machine but have NO entity-registry entry (common for legacy YAML and some
    # custom integrations). Iterating only the registry would silently drop them
    # from the "full-home" roster and its availability totals — merge them in.
    for state_obj in hass.states.async_all():
        if state_obj.entity_id in ent_reg.entities:
            continue  # already emitted from the registry pass above
        attrs = state_obj.attributes
        available = _available(state_obj.state)
        # Registry-less means enabled and user-facing (no disabled/hidden flag),
        # so an unusable state is genuinely broken.
        if not available:
            unavailable_total += 1
        if len(entities) < INSIGHTS_ROSTER_MAX_ENTITIES:
            entities.append(
                {
                    "entity_id": state_obj.entity_id,
                    "name": attrs.get("friendly_name") or state_obj.entity_id,
                    "domain": state_obj.domain,
                    "device_class": attrs.get("device_class") or "",
                    "area": "",
                    "state": state_obj.state,
                    "available": available,
                    "last_changed": state_obj.last_changed.isoformat(),
                    "disabled": False,
                    "hidden": False,
                    "device_id": None,
                }
            )
        else:
            truncated = True

    if truncated:
        _LOGGER.warning(
            "Home roster truncated at %d entities (install exceeds the cap)",
            INSIGHTS_ROSTER_MAX_ENTITIES,
        )

    # ── Devices ────────────────────────────────────────────────────────
    devices: list[RosterDevice] = []
    dev_count_by_entry: dict[str, int] = defaultdict(int)
    for dev in dev_reg.devices.values():
        primary_entry = dev.primary_config_entry or next(iter(dev.config_entries), None)
        integration = entry_meta.get(primary_entry, ("", "", ""))[0] if primary_entry else ""
        for entry_id in dev.config_entries:
            dev_count_by_entry[entry_id] += 1
        devices.append(
            {
                "id": dev.id,
                "name": dev.name_by_user or dev.name or "",
                "manufacturer": dev.manufacturer or "",
                "model": dev.model or "",
                "area": area_name(dev.area_id),
                "integration": integration,
                "disabled": dev.disabled_by is not None,
                "entities": ent_count_by_device.get(dev.id, 0),
                "unavailable_entities": unavail_by_device.get(dev.id, 0),
                "disabled_entities": disabled_by_device.get(dev.id, 0),
                "url": _strip_url_credentials(dev.configuration_url or ""),
            }
        )

    # ── Integrations ───────────────────────────────────────────────────
    integrations: list[RosterIntegration] = []
    for entry_id, (domain, title, state) in entry_meta.items():
        integrations.append(
            {
                "domain": domain,
                "name": integration_names.get(domain, ""),
                "title": title,
                "state": state,
                "devices": dev_count_by_entry.get(entry_id, 0),
                "entities": ent_count_by_entry.get(entry_id, 0),
                "has_issue": domain in issue_domains,
                "custom": domain in custom_domains,
                "url": _strip_url_credentials(integration_urls.get(domain, "")),
            }
        )

    # ── Automations / scripts / scenes ─────────────────────────────────
    automations: list[RosterAutomation] = []
    for st in hass.states.async_all("automation"):
        attrs = st.attributes
        alias = attrs.get("friendly_name") or st.entity_id
        selora = str(attrs.get("id", "")).startswith(AUTOMATION_ID_PREFIX) or str(alias).startswith(
            _SELORA_ALIAS_PREFIX
        )
        automations.append(
            {
                "entity_id": st.entity_id,
                "name": alias,
                "enabled": st.state == "on",
                "selora": selora,
                "last_triggered": _iso_or_none(attrs.get("last_triggered")),
            }
        )

    scripts: list[RosterScript] = []
    for st in hass.states.async_all("script"):
        scripts.append(
            {
                "entity_id": st.entity_id,
                "name": st.attributes.get("friendly_name") or st.entity_id,
                "state": st.state,
                "last_triggered": _iso_or_none(st.attributes.get("last_triggered")),
            }
        )

    scenes: list[RosterScene] = []
    for st in hass.states.async_all("scene"):
        scenes.append(
            {
                "entity_id": st.entity_id,
                "name": st.attributes.get("friendly_name") or st.entity_id,
            }
        )

    return {
        "integrations": integrations,
        "devices": devices,
        "entities": entities,
        "automations": automations,
        "scripts": scripts,
        "scenes": scenes,
        "truncated": truncated,
        "unavailable_total": unavailable_total,
        "disabled_total": disabled_total,
    }
