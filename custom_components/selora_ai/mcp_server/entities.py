"""MCP tools for reading the home: snapshot, devices, entity state, search, history, templates, analytics."""

from __future__ import annotations

from collections.abc import Mapping
import logging
import re
from typing import TYPE_CHECKING, Any

from homeassistant.core import HomeAssistant, State

from ..entity_capabilities import is_inspectable_entity
from ..helpers import device_entries
from ..lexical import (
    SEARCH_FUZZY_FLOOR,
    SEARCH_W_FUZZY,
    SEARCH_W_TERM_RATIO,
    fuzzy_ratio,
    normalize,
)
from .common import _sanitize

if TYPE_CHECKING:
    from homeassistant.helpers.device_registry import DeviceRegistry
    from homeassistant.helpers.entity_registry import RegistryEntry


_LOGGER = logging.getLogger(__name__)


# ── Tool: selora_get_home_snapshot ────────────────────────────────────────────


def _format_state_value(value: str) -> str:
    """Format entity state values for human-readable display.

    Converts ISO 8601 timestamps to 12-hour HH:MM AM/PM format.
    Other values are sanitized normally.
    """
    from ..helpers import format_entity_state

    raw = value.strip()
    result = format_entity_state(value)
    # format_entity_state returns the stripped input unchanged for
    # non-timestamps.  In that case, apply MCP sanitization to enforce
    # the 64-char limit on user-controlled state strings.
    if result == raw:
        return _sanitize(raw, limit=64)
    return result


# HA's own signal that a value is a secret: `input_text` and `text` entities
# carry `mode: password`, which is what makes the UI mask them. Their STATE is
# the value itself, so a bulk read hands a Wi-Fi key or an alarm code to the
# configured LLM and to any read-only MCP credential. The entity stays visible
# — an entity nobody can see is reported to its owner as absent, which is the
# failure this whole change is about — and only its value is withheld.
_REDACTED_STATE = "***"


def _display_state(state: State) -> str:
    """The state as an inventory read may show it, secrets withheld."""
    if str(state.attributes.get("mode", "")).strip().lower() == "password":
        return _REDACTED_STATE
    return _format_state_value(state.state)


async def _tool_get_home_snapshot(hass: HomeAssistant) -> dict[str, Any]:
    """Return current entity states grouped by HA area."""
    from homeassistant.helpers import area_registry as ar
    from homeassistant.helpers import device_registry as dr
    from homeassistant.helpers import entity_registry as er

    area_reg = ar.async_get(hass)
    entity_reg = er.async_get(hass)
    dev_reg = dr.async_get(hass)

    # Build area_id → area_name map
    area_names: dict[str, str] = {
        area.id: _sanitize(area.name) for area in area_reg.async_list_areas()
    }

    areas: dict[str, list[dict[str, Any]]] = {name: [] for name in area_names.values()}
    unassigned: list[dict[str, Any]] = []

    # An entity's own ``area_id`` is an OVERRIDE; the common case is no
    # override and the device's area. Reading only the entity's own put a home
    # where nobody had overridden anything in ``unassigned`` wholesale — a
    # snapshot grouped by area that reports the house as unassigned — and a
    # question about a room was then answered from an empty list.
    device_areas: dict[str, str] = {
        device.id: device.area_id for device in device_entries(dev_reg) if device.area_id
    }

    for state in hass.states.async_all():
        domain = state.entity_id.split(".")[0]
        # Every domain the home has, not the collector's analysis set: this
        # tool is what a caller asks when it wants to know what is here, and
        # an inventory that silently drops cameras is answered as "you have
        # none".
        if not is_inspectable_entity(state.entity_id):
            continue

        entry = entity_reg.async_get(state.entity_id)
        area_id = entry.area_id if entry else None
        if area_id is None and entry is not None and entry.device_id:
            area_id = device_areas.get(entry.device_id)

        entity_entry = {
            "entity_id": state.entity_id,
            "domain": domain,
            "state": _display_state(state),
            "friendly_name": _sanitize(state.attributes.get("friendly_name", state.entity_id)),
        }

        if area_id and area_id in area_names:
            areas[area_names[area_id]].append(entity_entry)
        else:
            unassigned.append(entity_entry)

    # Drop empty areas
    areas = {k: v for k, v in areas.items() if v}

    return {"areas": areas, "unassigned": unassigned}


# ── Tool: selora_list_devices ──────────────────────────────────────────────────


async def _tool_list_devices(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """List HA devices with optional area and domain filters."""
    from homeassistant.helpers import area_registry as ar
    from homeassistant.helpers import device_registry as dr
    from homeassistant.helpers import entity_registry as er

    dev_reg = dr.async_get(hass)
    ent_reg = er.async_get(hass)
    area_reg = ar.async_get(hass)

    area_filter: str = (arguments.get("area") or "").strip().lower()
    domain_filter: str = (arguments.get("domain") or "").strip().lower()

    # Build area_id → area_name map
    area_names: dict[str, str] = {area.id: area.name for area in area_reg.async_list_areas()}

    # Build config_entry_id → domain map for O(1) integration lookup
    entry_domains: dict[str, str] = {
        ce.entry_id: ce.domain for ce in hass.config_entries.async_entries()
    }

    devices: list[dict[str, Any]] = []
    for device in device_entries(dev_reg):
        # Resolve area name
        area_name = area_names.get(device.area_id or "") or ""

        # Apply area filter (case-insensitive substring match)
        if area_filter and area_filter not in area_name.lower():
            continue

        # Collect entities for this device in collector domains
        entities: list[dict[str, str]] = []
        device_domains: set[str] = set()
        for entity in er.async_entries_for_device(ent_reg, device.id):
            domain = entity.entity_id.split(".")[0]
            # A device's entities are what the device IS. Filtering them to the
            # collector's domains made a camera report as a handful of motion
            # sensors — and reads as authoritative, since the caller asked
            # about that device specifically.
            if is_inspectable_entity(entity.entity_id):
                state_obj = hass.states.get(entity.entity_id)
                entities.append(
                    {
                        "entity_id": entity.entity_id,
                        "state": _display_state(state_obj) if state_obj else "unknown",
                    }
                )
                device_domains.add(domain)

        # Skip devices with no entities in collector domains
        if not entities:
            continue

        # Apply domain filter
        if domain_filter and domain_filter not in device_domains:
            continue

        integration = _sanitize(entry_domains.get(device.primary_config_entry or "", ""))

        devices.append(
            {
                "device_id": device.id,
                "name": _sanitize(device.name or device.name_by_user or "Unknown"),
                "area": _sanitize(area_name) if area_name else None,
                "manufacturer": _sanitize(device.manufacturer or ""),
                "model": _sanitize(device.model or ""),
                "integration": integration,
                "domains": sorted(device_domains),
                "entities": entities,
            }
        )

    return {"devices": devices, "count": len(devices)}


# ── Tool: selora_get_device ────────────────────────────────────────────────────


async def _tool_get_device(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Return full detail for a single device with entity states."""
    from homeassistant.helpers import area_registry as ar
    from homeassistant.helpers import device_registry as dr
    from homeassistant.helpers import entity_registry as er

    from ..registry_manager import resolve_device  # noqa: PLC0415

    device_id = str(arguments.get("device_id", "")).strip()
    if not device_id:
        return {"error": "device_id is required"}

    # A registry id OR the device's user-visible name. Nothing but
    # ``list_devices`` hands out registry ids — ``search_entities`` and
    # ``get_entity_state`` resolve an entity, not its device — so an id-only
    # lookup leaves a caller that knows the device by name with no route here
    # at all, and the model then asks the user to paste one by hand.
    device, resolve_error = resolve_device(hass, device_id)
    if device is None:
        return {"error": resolve_error or f"Device {_sanitize(device_id)} not found"}

    ent_reg = er.async_get(hass)
    area_reg = ar.async_get(hass)

    # Resolve area
    area_name = ""
    if device.area_id:
        area = area_reg.async_get_area(device.area_id)
        area_name = area.name if area else ""

    # Resolve integration domain
    integration = ""
    if device.primary_config_entry:
        ce = hass.config_entries.async_get_entry(device.primary_config_entry)
        if ce is not None:
            integration = ce.domain

    # Collect entities with current states
    entities: list[dict[str, Any]] = []
    for entity in er.async_entries_for_device(ent_reg, device.id):
        domain = entity.entity_id.split(".")[0]
        if not is_inspectable_entity(entity.entity_id):
            continue

        state = hass.states.get(entity.entity_id)
        entity_entry: dict[str, Any] = {
            "entity_id": entity.entity_id,
            "domain": domain,
            "name": _sanitize(entity.name or entity.original_name or entity.entity_id),
            "state": _display_state(state) if state else "unavailable",
        }

        # Include key attributes based on domain
        if state and state.attributes:
            attrs = state.attributes
            filtered: dict[str, Any] = {}
            if "friendly_name" in attrs:
                filtered["friendly_name"] = _sanitize(attrs["friendly_name"])
            if "device_class" in attrs:
                filtered["device_class"] = str(attrs["device_class"])
            if "unit_of_measurement" in attrs:
                filtered["unit_of_measurement"] = str(attrs["unit_of_measurement"])
            # Domain-specific attributes
            if domain == "climate":
                for key in ("temperature", "current_temperature", "hvac_action"):
                    if key in attrs:
                        filtered[key] = attrs[key]
            elif domain == "light":
                for key in ("brightness", "color_temp", "color_mode"):
                    if key in attrs:
                        filtered[key] = attrs[key]
            elif domain == "cover":
                for key in ("current_position",):
                    if key in attrs:
                        filtered[key] = attrs[key]
            elif domain == "fan":
                for key in ("percentage", "preset_mode"):
                    if key in attrs:
                        filtered[key] = attrs[key]
            if filtered:
                entity_entry["attributes"] = filtered

        entities.append(entity_entry)

    # Hardware connection identifiers. ZHA button/sensor automations trigger on
    # `zha_event` with an `event_data.device_ieee`, which is NOT an entity or the
    # registry device_id — it lives in `device.connections` as
    # ("zigbee", "<ieee>"). Surface it explicitly (plus raw connections/
    # identifiers) so the LLM can build a working zha_event trigger instead of
    # asking the user to paste the IEEE by hand.
    # Sanitize the values like every other string field below: a malicious
    # integration could stash prompt-injection payloads in a connection or
    # identifier. A legitimate IEEE/MAC has no whitespace and is well under the
    # truncation limit, so it survives _sanitize unchanged and still feeds a
    # working zha_event trigger.
    connections = [[_sanitize(ctype), _sanitize(cval)] for ctype, cval in device.connections]
    identifiers = [[_sanitize(domain), _sanitize(ident)] for domain, ident in device.identifiers]
    zha_ieee = next(
        (_sanitize(cval) for ctype, cval in device.connections if ctype == dr.CONNECTION_ZIGBEE),
        None,
    )

    result: dict[str, Any] = {
        "device_id": device.id,
        "name": _sanitize(device.name or device.name_by_user or "Unknown"),
        "area": _sanitize(area_name) if area_name else None,
        "manufacturer": _sanitize(device.manufacturer or ""),
        "model": _sanitize(device.model or ""),
        "sw_version": _sanitize(device.sw_version or ""),
        "hw_version": _sanitize(device.hw_version or ""),
        "integration": _sanitize(integration),
        "via_device_id": device.via_device_id,
        "connections": connections,
        "identifiers": identifiers,
        "entities": entities,
    }
    if zha_ieee is not None:
        result["zha_ieee"] = zha_ieee
    return result


# ── Tool: selora_get_device_triggers ───────────────────────────────────────────


async def _tool_get_device_triggers(
    hass: HomeAssistant, arguments: dict[str, Any]
) -> dict[str, Any]:
    """Return the device triggers HA itself offers for a device.

    These are ready-to-use ``platform: device`` trigger blocks (correct domain,
    device_id, type, subtype) for button presses, scene-controller events, etc.
    They work across ZHA, Z-Wave JS, deCONZ, Shelly — any integration that
    registers device triggers — so the LLM never has to hand-assemble a
    protocol-specific event trigger or guess a raw node/IEEE id. The returned
    blocks can be dropped straight into an automation's ``triggers`` list.
    """
    from homeassistant.components.device_automation import (
        DeviceAutomationType,
        InvalidDeviceAutomationConfig,
        async_get_device_automations,
    )
    from homeassistant.exceptions import HomeAssistantError

    from ..registry_manager import resolve_device  # noqa: PLC0415

    device_ref = str(arguments.get("device_id", "")).strip()
    if not device_ref:
        return {"error": "device_id is required"}

    # Accepts a name as well as a registry id, for the reason ``get_device``
    # does: this is the tool the trigger prompt sends the model to for a
    # button/remote/doorbell, and the id it asks for is not in anything the
    # model has been given.
    device, resolve_error = resolve_device(hass, device_ref)
    if device is None:
        return {"error": resolve_error or f"Device {_sanitize(device_ref)} not found"}
    device_id = device.id

    try:
        automations = await async_get_device_automations(
            hass, DeviceAutomationType.TRIGGER, [device_id]
        )
    except (InvalidDeviceAutomationConfig, HomeAssistantError) as exc:
        # A device with no valid triggers is not an error — report an empty list
        # so the LLM falls back to an event/state trigger instead of stalling.
        _LOGGER.debug("Device trigger lookup failed for %s: %s", device_id, exc)
        return {"device_id": device_id, "triggers": [], "count": 0}

    triggers = [dict(trigger) for trigger in automations.get(device_id, [])]
    return {"device_id": device_id, "triggers": triggers, "count": len(triggers)}


# ── Tool: selora_get_entity_state ──────────────────────────────────────────────


# Domain-specific attribute whitelist for get_entity_state. Keeps the response
# small and avoids leaking diagnostic noise into the LLM context. Mirrors the
# selection used in _tool_get_device.
# An attribute named like a credential is left out, and a URL keeps its path
# but loses these parameters: a camera's ``entity_picture`` carries the token
# that opens its stream, and this read is open to read-only credentials while
# camera images are admin-only.
_SECRET_ATTR = re.compile(r"token|password|passcode|secret|api_?key", re.IGNORECASE)
# Looks behind for its separator rather than consuming it, so adjacent
# parameters (``?token=a&access_token=b``) are each removed.
_URL_SECRET_PARAM = re.compile(r"(?<=[?&])(?:token|authSig|access_token)=[^&#]*&?", re.IGNORECASE)
_ATTR_TEXT_LIMIT = 300
_ATTR_LIST_LIMIT = 50
_ATTR_DEPTH = 3
# Every attribute is returned until this budget, then the rest are named in
# ``attributes_omitted``: some integrations put whole JSON payloads in one.
# It is charged while converting, so an oversized one is abandoned early
# rather than built in full and then dropped.
_ATTRS_MAX_CHARS = 6000


class _OverBudget(Exception):
    """The attribute being converted does not fit what is left."""


class _AttrConverter:
    """Attribute values as bounded, JSON-encodable data, within a budget.

    Sets become sorted lists and enum members their string value (the
    dispatcher's ``json.dumps`` has no ``default=``); text is sanitized and
    capped, lists and mappings cut at ``_ATTR_LIST_LIMIT`` entries before any
    is converted, and nesting past ``_ATTR_DEPTH`` replaced, not stringified —
    a stringified mapping would carry its credential keys along.
    """

    def __init__(self, budget: int) -> None:
        self.left = budget

    def _charge(self, size: int) -> None:
        self.left -= size
        if self.left < 0:
            raise _OverBudget

    def convert(self, value: Any, depth: int = 0) -> Any:
        if isinstance(value, bool | int | float) or value is None:
            self._charge(8)
            return value
        if isinstance(value, str):
            text = _URL_SECRET_PARAM.sub("", value[: _ATTR_TEXT_LIMIT * 4]).rstrip("?&")
            text = _sanitize(text, _ATTR_TEXT_LIMIT)
            self._charge(len(text) + 2)
            return text
        if isinstance(value, Mapping):
            if depth >= _ATTR_DEPTH:
                self._charge(5)
                return "{…}"
            out: dict[str, Any] = {}
            for count, (key, item) in enumerate(value.items()):
                if count >= _ATTR_LIST_LIMIT:
                    out["…"] = f"{len(value) - _ATTR_LIST_LIMIT} more"
                    break
                name = str(key)[:64]
                if _SECRET_ATTR.search(name):
                    continue
                self._charge(len(name) + 4)
                out[name] = self.convert(item, depth + 1)
            return out
        if isinstance(value, set | frozenset | tuple | list):
            if depth >= _ATTR_DEPTH:
                self._charge(5)
                return "[…]"
            items = list(value)
            if isinstance(value, set | frozenset):
                items.sort(key=str)
            extra = len(items) - _ATTR_LIST_LIMIT
            out_list = [self.convert(v, depth + 1) for v in items[:_ATTR_LIST_LIMIT]]
            if extra > 0:
                out_list.append(f"… {extra} more")
            return out_list
        if hasattr(value, "isoformat"):
            self._charge(32)
            return value.isoformat()
        return self.convert(str(value), depth)


def _entity_attributes(attrs: Mapping[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Every attribute that fits the budget, and the names of those that did not."""
    kept: dict[str, Any] = {}
    omitted: list[str] = []
    left = _ATTRS_MAX_CHARS
    for key, raw in attrs.items():
        name = str(key)
        if _SECRET_ATTR.search(name):
            continue
        converter = _AttrConverter(left - len(name))
        try:
            kept[name] = converter.convert(raw)
        except _OverBudget:
            omitted.append(_sanitize(name, 64))
            continue
        left = converter.left
    return kept, omitted


async def _tool_get_entity_state(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Return current state, attributes and exposure for a single entity."""
    from homeassistant.helpers import area_registry as ar
    from homeassistant.helpers import device_registry as dr
    from homeassistant.helpers import entity_registry as er

    entity_id = str(arguments.get("entity_id", "")).strip()
    if not entity_id:
        return {"error": "entity_id is required"}
    if "." not in entity_id:
        return {"error": f"entity_id '{_sanitize(entity_id)}' is malformed"}

    state = hass.states.get(entity_id)
    if state is None:
        return {"error": f"entity '{_sanitize(entity_id)}' not found"}

    ent_reg = er.async_get(hass)
    area_reg = ar.async_get(hass)
    entry = ent_reg.async_get(entity_id)

    # Resolve area: entity.area_id first, then fall back to the entity's
    # device.area_id. Many HA setups assign rooms at the device level only
    # (Hue bulbs, ESPHome boards, etc.), so without this fallback common
    # entities show area=null and the LLM loses room context in answers.
    area_name: str | None = None
    ent_area_id: str | None = entry.area_id if entry else None
    # The owning device, so a caller holding only an entity_id can reach
    # ``get_device`` / ``get_device_triggers``. The lookup is already here for
    # the area fallback; returning it is what saves a ``list_devices`` dump.
    ent_device_id: str | None = entry.device_id if entry else None
    if ent_area_id is None and entry and entry.device_id:
        device = dr.async_get(hass).async_get(entry.device_id)
        if device:
            ent_area_id = device.area_id
    if ent_area_id:
        area = area_reg.async_get_area(ent_area_id)
        if area:
            area_name = area.name

    from ..entity_exposure import async_get_exposure  # noqa: PLC0415

    domain = entity_id.split(".", 1)[0]
    attrs = state.attributes or {}
    attributes, omitted = _entity_attributes(attrs)
    extra: dict[str, Any] = {}
    if omitted:
        extra["attributes_omitted"] = omitted
    if exposed := async_get_exposure(hass, entity_id):
        extra["exposed_to"] = exposed
    if entry is not None and (aliases := [a for a in entry.aliases if isinstance(a, str)]):
        extra["aliases"] = [_sanitize(a, 60) for a in aliases]

    return {
        "entity_id": entity_id,
        "domain": domain,
        "state": _display_state(state),
        "friendly_name": _sanitize(attrs.get("friendly_name", entity_id)),
        "area": _sanitize(area_name) if area_name else None,
        "device_id": ent_device_id,
        "last_changed": state.last_changed.isoformat() if state.last_changed else None,
        "last_updated": state.last_updated.isoformat() if state.last_updated else None,
        "attributes": attributes,
        **extra,
    }


# ── Tool: selora_find_entities_by_area ─────────────────────────────────────────


async def _tool_find_entities_by_area(
    hass: HomeAssistant, arguments: dict[str, Any]
) -> dict[str, Any]:
    """Return entities in a given area, optionally filtered by domain."""
    from homeassistant.helpers import area_registry as ar
    from homeassistant.helpers import entity_registry as er

    area_filter = str(arguments.get("area", "")).strip().lower()
    domain_filter = str(arguments.get("domain", "")).strip().lower()
    if not area_filter:
        return {"error": "area is required"}

    area_reg = ar.async_get(hass)
    ent_reg = er.async_get(hass)
    dev_reg = None  # lazy

    # Resolve matching area_ids (case-insensitive substring match)
    matching_area_ids: dict[str, str] = {}
    for area in area_reg.async_list_areas():
        if area_filter in area.name.lower():
            matching_area_ids[area.id] = area.name

    if not matching_area_ids:
        return {"entities": [], "count": 0, "area_matches": []}

    entities: list[dict[str, Any]] = []
    for state in hass.states.async_all():
        if not is_inspectable_entity(state.entity_id):
            continue
        domain = state.entity_id.split(".", 1)[0]
        if domain_filter and domain != domain_filter:
            continue

        entry = ent_reg.async_get(state.entity_id)
        if entry is None:
            continue

        # Resolve entity area via entity.area_id, falling back to its device.area_id
        ent_area_id = entry.area_id
        if ent_area_id is None and entry.device_id:
            if dev_reg is None:
                from homeassistant.helpers import device_registry as dr

                dev_reg = dr.async_get(hass)
            device = dev_reg.async_get(entry.device_id)
            if device:
                ent_area_id = device.area_id

        if ent_area_id not in matching_area_ids:
            continue

        entities.append(
            {
                "entity_id": state.entity_id,
                "domain": domain,
                "state": _display_state(state),
                "friendly_name": _sanitize(state.attributes.get("friendly_name", state.entity_id)),
                "area": _sanitize(matching_area_ids[ent_area_id]),
            }
        )

    return {
        "entities": entities,
        "count": len(entities),
        "area_matches": sorted(_sanitize(name) for name in matching_area_ids.values()),
    }


# ── Tool: selora_search_entities ───────────────────────────────────────────────

# What the haystack is built from, reported back when nothing matched so the
# caller can tell a field it did not search from a device that is not there.
_SEARCH_FIELDS = "entity_id, friendly name, aliases, area, device name/manufacturer/model"

# An empty result is a failed LOOKUP, and the model that receives it has no
# other view of the home to check it against — battery and other diagnostic
# entities are filtered out of the snapshot entirely, so this tool is the only
# route to them. Read as absence, "no match for IKEA" becomes "you have no IKEA
# battery entities, add them and ask me again" — told to a user whose IKEA
# sensors are sitting right there, named after the product rather than the
# brand. So the empty result says what it means and what to try instead.
# A ranked search is a resolution: the caller wants the entity it named, and a
# long tail of weaker matches is noise. A FILTER-ONLY call is a listing — every
# battery in the house, which is the whole of "notify me when batteries are
# low" — so it gets its own, larger ceiling and returns the lot by default.
# Both are bounded: the result shares a 16K budget with the rest of the turn,
# and past the bound ``omitted`` says how many rows are missing rather than
# letting the list read as complete.
_MAX_RANKED_MATCHES = 25
_MAX_LISTING_MATCHES = 50
_DEFAULT_RANKED_MATCHES = 10

_NO_MATCH_HINT = (
    "No entity matched. This is a name lookup coming up empty, NOT evidence "
    "that the device is absent — do not tell the user it does not exist on "
    "this alone. Try again with one distinctive word rather than a phrase, "
    "with the product name rather than the brand, with a device_class filter "
    "(device_class='battery' needs no query at all), or call list_devices to "
    "see what is actually installed."
)


def _entity_term_count(query_terms: list[str], haystack: str) -> int:
    """Count how many query terms appear literally in the haystack.

    One signal of the search ranking ensemble; the fuzzy component is
    blended at the call site (see :func:`_tool_search_entities`).
    """
    return sum(1 for term in query_terms if term in haystack)


def _device_search_index(dev_reg: DeviceRegistry) -> dict[str, dict[str, str]]:
    """Per-device searchable text and area, from one registry walk.

    An entity's own names carry the product and never the brand: "IKEA" lives
    on the DEVICE as ``manufacturer`` ("IKEA of Sweden"), and the model number
    beside it, while the entity is called "TIMMERFLOTTE temp/hmd sensor
    Battery". Indexing the entity alone answers a brand query with nothing —
    and nothing is the one answer a caller cannot act on. So the device's
    name, user-given name, manufacturer and model join the haystack, which is
    also what keeps ``list_devices`` (a whole-home dump this tool exists to
    avoid) from being the only way to reach a brand's entities.

    One walk per call rather than a registry hit per entity: a home has far
    fewer devices than entities, and the same walk carries the area fallback
    the loop needs for an entity whose own ``area_id`` is unset.
    """
    index: dict[str, dict[str, str]] = {}
    for device in device_entries(dev_reg):
        manufacturer = device.manufacturer or ""
        model = device.model or ""
        index[device.id] = {
            "text": normalize(
                " ".join(
                    part
                    for part in (
                        device.name_by_user or "",
                        device.name or "",
                        manufacturer,
                        model,
                    )
                    if part
                )
            ),
            "area_id": device.area_id or "",
            "manufacturer": manufacturer,
            "model": model,
        }
    return index


def _entity_device_class(state: State, entry: RegistryEntry | None) -> str:
    """The entity's device class — live attribute first, registry behind it.

    The attribute is what the entity reports now and already reflects a user
    override, but an unavailable entity can drop it while the registry still
    knows what it is, and a low-battery request must not miss a sensor whose
    device is asleep.
    """
    live = state.attributes.get("device_class")
    if live:
        return str(live).strip().lower()
    if entry is not None:
        stored = entry.device_class or entry.original_device_class
        if stored:
            return str(stored).strip().lower()
    return ""


async def _tool_search_entities(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Fuzzy search entities across their own names, their area, and their device."""
    from homeassistant.helpers import area_registry as ar
    from homeassistant.helpers import device_registry as dr
    from homeassistant.helpers import entity_registry as er

    query = normalize(str(arguments.get("query", "")))
    domain_filter = str(arguments.get("domain", "")).strip().lower()
    device_class_filter = str(arguments.get("device_class", "")).strip().lower()

    query_terms = [t for t in query.split() if t]
    # Any filter may stand alone; only a call carrying none of the three is
    # refused. "All my cameras" and "every battery entity" are real requests
    # with no name to search for — the latter is where a low-battery
    # automation starts — and refusing them sent the model back to the user
    # saying it could not do it. What made a bare filter dangerous was an
    # unbounded dump; the listing ceiling and `omitted` below bound it.
    if not query_terms and not device_class_filter and not domain_filter:
        return {
            "error": (
                "query is required, or filter alone by domain and/or device_class "
                "(e.g. domain='camera', device_class='battery')"
            )
        }

    # A filter-only call is a listing, so it defaults to its whole ceiling: a
    # low-battery automation that silently saw the first ten of a home's forty
    # batteries is the same failure as the search that found none of them.
    listing = not query_terms
    ceiling = _MAX_LISTING_MATCHES if listing else _MAX_RANKED_MATCHES
    default_limit = ceiling if listing else _DEFAULT_RANKED_MATCHES
    try:
        limit = int(arguments.get("limit", default_limit))
    except (
        TypeError,
        ValueError,
    ):
        limit = default_limit
    limit = max(1, min(limit, ceiling))

    ent_reg = er.async_get(hass)
    area_reg = ar.async_get(hass)
    dev_reg = dr.async_get(hass)
    area_names: dict[str, str] = {a.id: a.name for a in area_reg.async_list_areas()}
    device_index = _device_search_index(dev_reg)

    scored: list[tuple[float, dict[str, Any]]] = []
    for state in hass.states.async_all():
        # Every domain the home has. A scene, a camera and a pending update
        # are all things a caller resolves by name — this search is how the
        # architect maps "Stores at 50%" to a real `scene.*` id, and guessing
        # one instead fails automation validation as an unknown entity_id.
        # Restricting it to the collector's analysis domains answered "all my
        # cameras" with nothing at all.
        if not is_inspectable_entity(state.entity_id):
            continue
        domain = state.entity_id.split(".", 1)[0]
        if domain_filter and domain != domain_filter:
            continue

        entry = ent_reg.async_get(state.entity_id)
        friendly = str(state.attributes.get("friendly_name", "")).lower()
        aliases = ""
        ent_area_id: str | None = None
        # The entity's device, so a caller that found the entity here can reach
        # the device tools without a whole-home ``list_devices`` dump.
        ent_device_id: str | None = None
        if entry is not None:
            # `compat_aliases` is the plain-string view added in HA
            # 2026.x where `aliases` became list[AliasEntry]
            # (str | ComputedNameType). Older HA exposes `aliases` as
            # set[str], so fall back to it when compat_aliases is absent.
            raw_aliases = getattr(entry, "compat_aliases", None)
            if raw_aliases is None:
                raw_aliases = entry.aliases
            aliases = " ".join(str(a) for a in raw_aliases).lower() if raw_aliases else ""
            ent_area_id = entry.area_id
            ent_device_id = entry.device_id

        device_info = device_index.get(ent_device_id or "", {})
        if ent_area_id is None:
            ent_area_id = device_info.get("area_id") or None

        device_class = _entity_device_class(state, entry)
        if device_class_filter and device_class != device_class_filter:
            continue

        area_name = area_names.get(ent_area_id or "", "")
        own_haystack = normalize(" ".join([state.entity_id, friendly, aliases, area_name]))
        device_text = device_info.get("text", "")
        haystack = f"{own_haystack} {device_text}" if device_text else own_haystack
        # Ensemble rank: literal term coverage + order-insensitive fuzzy
        # similarity. ``score`` stays a plain term-hit count for the LLM;
        # ``rank`` (fuzzy-blended) drives ordering and admits typo-only
        # matches that have zero literal hits.
        #
        # Term coverage reads the device text; the fuzzy component stays on the
        # entity's OWN names. Fuzzy is the typo rescue, and a token-set ratio
        # against a longer haystack scores the same typo lower — folding every
        # device's name, brand and model in would raise SEARCH_FUZZY_FLOOR out
        # from under the queries it exists for, trading a typo'd room name for
        # a brand match that literal coverage already catches.
        term_hits = _entity_term_count(query_terms, haystack)
        if query_terms:
            fuzzy = fuzzy_ratio(query, own_haystack)
            if term_hits == 0 and fuzzy < SEARCH_FUZZY_FLOOR:
                continue
            rank = SEARCH_W_TERM_RATIO * (term_hits / len(query_terms)) + SEARCH_W_FUZZY * fuzzy
        else:
            # device_class alone: every entity past the filters is a match and
            # there is nothing to rank them by, so they sort by entity_id.
            rank = 0.0

        match: dict[str, Any] = {
            "entity_id": state.entity_id,
            "domain": domain,
            "state": _display_state(state),
            "friendly_name": _sanitize(state.attributes.get("friendly_name", state.entity_id)),
            "area": _sanitize(area_names.get(ent_area_id or "", "")) or None,
            "device_id": ent_device_id,
            "score": term_hits,
        }
        # The fields that made a brand or class query match, echoed back so the
        # caller can tell which rows it actually asked for — a fuzzy search
        # for "IKEA" returns near misses too, and nothing else in the row says
        # which is which. Omitted when unset rather than sent empty: the rows
        # share the tool-result budget with up to `limit` of their neighbours.
        manufacturer = _sanitize(device_info.get("manufacturer", ""))
        if manufacturer:
            match["manufacturer"] = manufacturer
        model = _sanitize(device_info.get("model", ""))
        if model:
            match["model"] = model
        if device_class:
            match["device_class"] = device_class

        scored.append((rank, match))

    scored.sort(key=lambda x: (-x[0], x[1]["entity_id"]))
    matches = [item for _, item in scored[:limit]]
    result: dict[str, Any] = {
        "matches": matches,
        "count": len(matches),
        "total_scored": len(scored),
    }
    # Stated outright rather than left to be inferred from `total_scored`: a
    # caller listing a device class is assembling a complete set, and a partial
    # list that does not say so is used as though it were the whole home.
    if len(scored) > len(matches):
        result["omitted"] = len(scored) - len(matches)
        result["omitted_note"] = (
            f"Showing {len(matches)} of {len(scored)} matches. Raise `limit` "
            f"(max {ceiling}) or narrow the search to see the rest."
        )
    if not scored:
        result["searched"] = _SEARCH_FIELDS
        result["hint"] = _NO_MATCH_HINT
    return result


# ── Tool: selora_get_entity_history ────────────────────────────────────────────


async def _tool_get_entity_history(
    hass: HomeAssistant, arguments: dict[str, Any]
) -> dict[str, Any]:
    """State changes over a time range, or long-term statistics — see ``history_reader``."""
    from ..history_reader import async_read_history  # noqa: PLC0415

    return await async_read_history(hass, arguments)


# ── Tool: selora_eval_template ─────────────────────────────────────────────────


_TEMPLATE_MAX_CHARS = 1024


async def _tool_eval_template(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Evaluate a Home Assistant Jinja template using HA's sandbox."""
    from homeassistant.exceptions import TemplateError
    from homeassistant.helpers.template import Template

    raw = arguments.get("template")
    if not isinstance(raw, str) or not raw.strip():
        return {"error": "template is required"}
    if len(raw) > _TEMPLATE_MAX_CHARS:
        return {"error": f"template exceeds {_TEMPLATE_MAX_CHARS}-character limit"}

    try:
        tpl = Template(raw, hass)
        result = tpl.async_render(parse_result=False)
    except TemplateError as exc:
        return {"error": f"template error: {exc}"}
    except Exception as exc:  # noqa: BLE001
        _LOGGER.exception("eval_template failed")
        return {"error": f"template evaluation failed: {exc}"}

    return {"result": _sanitize(result, limit=_TEMPLATE_MAX_CHARS)}


# ── Tool: selora_home_analytics ──────────────────────────────────────────────


async def _tool_home_analytics(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Get analytics about device usage patterns and state changes."""
    from ..pattern_store import get_pattern_store  # noqa: PLC0415

    pattern_store = get_pattern_store(hass)
    if pattern_store is None:
        return {"error": "Pattern store not available"}

    entity_id = str(arguments.get("entity_id", "")).strip() or None

    if entity_id:
        usage_windows = await pattern_store.get_usage_windows(entity_id)
        state_transitions = await pattern_store.get_state_transition_counts(entity_id)
        return {
            "entity_id": entity_id,
            "usage_windows": [
                {**w, "primary_state": _sanitize(w["primary_state"], limit=64)}
                for w in usage_windows
            ],
            "state_transitions": [
                {
                    "from": _sanitize(t["from"], limit=64),
                    "to": _sanitize(t["to"], limit=64),
                    "count": t["count"],
                }
                for t in state_transitions
            ],
        }

    return await pattern_store.get_analytics_summary()
