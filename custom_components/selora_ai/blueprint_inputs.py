"""Check a blueprint automation's inputs before it is written.

Home Assistant validates a ``use_blueprint`` automation only when automations
reload — after the file is written and the write reported a success — and
even then it checks only that required inputs are present. A misspelled input
is silently ignored (the blueprint falls back to its default), a value of the
wrong shape surfaces as a broken automation, and an entity that does not exist
is accepted. So, before writing:

* the path must be a loadable AUTOMATION blueprint (``_blueprint_path_error``);
* every supplied input must be one the blueprint declares;
* each value must satisfy the input's own selector, checked by HA's selector
  schemas rather than a copy of their rules;
* an entity named through an ``entity`` or ``target`` selector must exist;
* the substituted automation must pass HA's own automation validator, which
  is also what reports a required input left out.

Skipped when blueprints are not set up: a missing store is not evidence the
automation is wrong.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from homeassistant.const import ENTITY_MATCH_ALL, ENTITY_MATCH_NONE
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import entity_sources
import voluptuous as vol

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)


def _describe_inputs(declared: dict[str, Any]) -> str:
    names = [
        f"{name} (required)" if not (isinstance(spec, dict) and "default" in spec) else name
        for name, spec in declared.items()
    ]
    return ", ".join(names) or "nothing"


def _entity_ids(selector_config: dict[str, Any], value: Any) -> set[str]:
    """The entity ids a value names, for the selectors that name entities."""
    if "entity" in selector_config:
        raw = value
    elif "target" in selector_config and isinstance(value, dict):
        raw = value.get("entity_id")
    else:
        return set()
    values = raw if isinstance(raw, list) else [raw]
    # "light.a, light.b" is Home Assistant's shorthand for two entities.
    values = [
        part.strip()
        for v in values
        for part in (v.split(",") if isinstance(v, str) and "{" not in v else [v])
    ]
    # A template or a reference to another input resolves at run time, and a
    # target may say "all" or "none" rather than name an entity.
    return {
        v
        for v in values
        if isinstance(v, str)
        and v
        and not any(mark in v for mark in ("{{", "{%", "{#"))
        and v not in (ENTITY_MATCH_ALL, ENTITY_MATCH_NONE)
    }


def _entity_filters(selector_config: dict[str, Any]) -> list[dict[str, Any]]:
    """The entity filters an entity or target selector carries: ``filter`` (one
    or a list), the older top-level keys, or a target's ``entity`` (one or a
    list). Empty means any entity.

    ``supported_features`` is not enforced: its values are feature NAMES whose
    resolution to bits is private to HA's selector module, and a wrong guess
    would refuse valid inputs."""
    if isinstance(entity := selector_config.get("entity"), dict):
        raw: Any = entity.get("filter", entity)
    elif isinstance(target := selector_config.get("target"), dict):
        raw = target.get("entity")
    else:
        return []
    filters = raw if isinstance(raw, list) else [raw]
    keys = ("domain", "integration", "device_class", "unit_of_measurement", "device")
    return [f for f in filters if isinstance(f, dict) and any(k in f for k in keys)]


def _as_set(value: Any) -> set[str]:
    return {str(v) for v in value} if isinstance(value, list) else {str(value)}


def _matches(hass: HomeAssistant, entity_id: str, wanted: dict[str, Any]) -> bool:
    from homeassistant.helpers import entity_registry as er  # noqa: PLC0415

    entry = er.async_get(hass).async_get(entity_id)
    # A registry UUID is a valid way to name an entity; the filters read the
    # entity it resolves to.
    if entry is not None:
        entity_id = entry.entity_id
    state = hass.states.get(entity_id)
    if "domain" in wanted and entity_id.split(".", 1)[0] not in _as_set(wanted["domain"]):
        return False
    if "integration" in wanted:
        # An entity with no registry entry still records its integration in
        # the entity sources, as the frontend's filter reads it.
        source = entity_sources(hass).get(entity_id, {})
        platform = entry.platform if entry is not None else source.get("domain")
        if platform != wanted["integration"]:
            return False
    if "device_class" in wanted:
        device_class = (
            (entry.device_class or entry.original_device_class) if entry is not None else None
        ) or (state.attributes.get("device_class") if state is not None else None)
        if device_class not in _as_set(wanted["device_class"]):
            return False
    if "unit_of_measurement" in wanted:
        unit = (entry.unit_of_measurement if entry is not None else None) or (
            state.attributes.get("unit_of_measurement") if state is not None else None
        )
        if unit not in _as_set(wanted["unit_of_measurement"]):
            return False
    if isinstance(device_filter := wanted.get("device"), dict | list):
        return _device_matches(hass, entry, device_filter)
    return True


def _device_matches(hass: HomeAssistant, entry: Any, device_filter: Any) -> bool:
    """The entity's device against a nested device filter (any of a list):
    integration, manufacturer, model, model_id."""
    from homeassistant.helpers import device_registry as dr  # noqa: PLC0415

    device = (
        dr.async_get(hass).async_get(entry.device_id)
        if entry is not None and entry.device_id
        else None
    )
    if device is None:
        return False
    domains = {
        config_entry.domain
        for entry_id in device.config_entries
        if (config_entry := hass.config_entries.async_get_entry(entry_id)) is not None
    }
    filters = device_filter if isinstance(device_filter, list) else [device_filter]
    for wanted in filters:
        if not isinstance(wanted, dict):
            continue
        if "integration" in wanted and wanted["integration"] not in domains:
            continue
        if any(
            key in wanted and str(getattr(device, key, None)) != str(wanted[key])
            for key in ("manufacturer", "model", "model_id")
        ):
            continue
        return True
    return False


def _filter_error(
    hass: HomeAssistant, name: str, selector_config: dict[str, Any], entity_ids: set[str]
) -> str | None:
    """The entity the selector's filters rule out — a switch given to a light
    input passes its shape and exists, and the blueprint then acts on it."""
    if not (filters := _entity_filters(selector_config)):
        return None
    for entity_id in sorted(entity_ids):
        if (hass.states.get(entity_id) is not None or _registered(hass, entity_id)) and not any(
            _matches(hass, entity_id, f) for f in filters
        ):
            return f"Input '{name}' takes only entities matching {filters}; {entity_id} does not."
    return None


def _registered(hass: HomeAssistant, entity_id: str) -> bool:
    from homeassistant.helpers import entity_registry as er  # noqa: PLC0415

    return er.async_get(hass).async_get(entity_id) is not None


def _inputs_error(
    hass: HomeAssistant, path: str, declared: dict[str, Any], supplied: dict[str, Any]
) -> str | None:
    from homeassistant.helpers import selector as ha_selector  # noqa: PLC0415

    from .automation_utils import _find_unknown_entity_ids  # noqa: PLC0415

    if bad := [k for k in supplied if not isinstance(k, str)]:
        return f"use_blueprint.input names must be text; got {bad[:3]!r}."
    if undeclared := sorted(set(supplied) - set(declared)):
        return (
            f"use_blueprint.input names {', '.join(undeclared)}, which '{path}' does not "
            f"declare; an undeclared input is ignored. It takes: {_describe_inputs(declared)}."
        )

    referenced: set[str] = set()
    # What the blueprint will run with: an omitted input takes its default,
    # and a default naming an entity this home lacks fails just as quietly.
    # An empty default ("", [], {}) is how an optional input says "none".
    defaulted = {
        name: spec["default"]
        for name, spec in declared.items()
        if name not in supplied
        and isinstance(spec, dict)
        and spec.get("default") not in (None, "", [], {})
    }
    for name, value in {**defaulted, **supplied}.items():
        spec = declared.get(name)
        selector_config = spec.get("selector") if isinstance(spec, dict) else None
        if not isinstance(selector_config, dict) or not selector_config:
            continue
        hint = " (its default; set the input explicitly)" if name in defaulted else ""
        try:
            ha_selector.selector(selector_config)(value)
        except (vol.Invalid, HomeAssistantError) as exc:
            return f"Input '{name}' does not fit its selector {selector_config}: {exc}{hint}"
        except (KeyError, TypeError, ValueError) as exc:
            # A selector this core does not know: the blueprint's problem, and
            # HA's reload will say so; not a reason to refuse the inputs.
            _LOGGER.debug("Skipped selector check for input %s: %s", name, exc)
            continue
        named = _entity_ids(selector_config, value)
        if error := _filter_error(hass, name, selector_config, named):
            return error + hint
        if hint and (missing := _find_unknown_entity_ids(hass, named)):
            return (
                f"Input '{name}' was left to its default, {', '.join(missing)}, which this "
                "home does not have; set the input explicitly."
            )
        referenced |= named

    if unknown := _find_unknown_entity_ids(hass, referenced):
        preview = ", ".join(unknown[:3])
        suffix = f" (+{len(unknown) - 3} more)" if len(unknown) > 3 else ""
        # Worded as the ordinary automation check is, so the chat correction
        # loop names the closest real entities for these too.
        return f"automation references unknown entity_id(s): {preview}{suffix}"
    return None


async def async_blueprint_error(
    hass: HomeAssistant, use_blueprint: dict[str, Any], alias: str = ""
) -> str | None:
    """Why this ``use_blueprint`` automation would not work, or None."""
    from .automation_utils import (  # noqa: PLC0415
        _blueprint_path_error,
        _home_assistant_validation_error,
    )

    path = str(use_blueprint.get("path", ""))
    if path != path.strip():
        # Written as given, and HA cannot resolve it with the spaces.
        return f"use_blueprint.path {path!r} has surrounding spaces; remove them."
    if error := await _blueprint_path_error(hass, path):
        return error

    stores = hass.data.get("blueprint")
    store = stores.get("automation") if isinstance(stores, dict) else None
    if store is None:
        return None
    try:
        blueprint = await store.async_get_blueprint(path)
    except (HomeAssistantError, OSError, ValueError) as exc:
        # A missing or broken file was named by _blueprint_path_error; a read
        # that fails now is the store's problem, and it lets the write through
        # just as that probe does.
        _LOGGER.debug("Blueprint %s could not be read for the input check: %s", path, exc)
        return None

    supplied = use_blueprint.get("input") or {}
    if not isinstance(supplied, dict):
        return "use_blueprint input must be an object"
    if error := _inputs_error(hass, path, dict(blueprint.inputs or {}), supplied):
        return error

    return await _home_assistant_validation_error(
        hass,
        "selora_blueprint_check",
        {"alias": alias or "Blueprint check", "use_blueprint": {"path": path, "input": supplied}},
    )
