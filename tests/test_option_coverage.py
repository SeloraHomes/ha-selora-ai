"""Every option Home Assistant lets a user set is one our tools can set — or a
listed, reasoned exception.

The surprises users met were options Home Assistant had and our tools did not:
an area's temperature sensor, Alexa exposure, a script's fields, a page's
theme. Each was found by someone asking. These read Home Assistant's own
schemas — the websocket commands its settings pages call, its automation and
script schemas, each helper's create schema — so when the test-stack pin moves
to a core with a new option, CI fails here and the option is decided on: given
a tool, or added below with the reason it is left out.

A failure here is not a regression. It is Home Assistant growing; read the new
option's name, decide, and record the decision.
"""

from __future__ import annotations

import importlib
from typing import Any

from homeassistant.core import HomeAssistant
import pytest

from custom_components.selora_ai.automation_utils import validate_automation_payload
from custom_components.selora_ai.helper_manager import (
    CREATABLE_HELPER_DOMAINS,
    _create_schema,
    _schema_keys,
)
from custom_components.selora_ai.tool_executor import create_helper_fields
from custom_components.selora_ai.tool_registry import TOOL_CREATE_HELPER, TOOL_MAP

_NEW_OPTION = (
    "Home Assistant has {kind} option(s) {names} that no Selora tool sets. Give a "
    "tool the option, or add it to {table} in this file with the reason it is left out."
)

# ── Registry settings (what Settings → … → the settings dialog can change) ──

# (websocket command module, handler, fields that only address the item)
_REGISTRY_COMMANDS: dict[str, tuple[str, str, set[str]]] = {
    "entity": (
        "homeassistant.components.config.entity_registry",
        "websocket_update_entity",
        {"entity_id"},
    ),
    "device": (
        "homeassistant.components.config.device_registry",
        "websocket_update_device",
        {"device_id"},
    ),
    "area": ("homeassistant.components.config.area_registry", "websocket_update_area", {"area_id"}),
    "floor": (
        "homeassistant.components.config.floor_registry",
        "websocket_update_floor",
        {"floor_id"},
    ),
    "label": (
        "homeassistant.components.config.label_registry",
        "websocket_update_label",
        {"label_id"},
    ),
    "category": (
        "homeassistant.components.config.category_registry",
        "websocket_update_category",
        {"category_id", "scope"},
    ),
}

# Field → the tool parameter that sets it.
_REGISTRY_COVERED: dict[str, dict[str, str]] = {
    "entity": {
        "aliases": "update_entity.aliases",
        "area_id": "assign_area / update_entity.clear=['area']",
        "categories": "assign_category",
        "disabled_by": "update_entity.disabled",
        "hidden_by": "update_entity.hidden",
        "icon": "update_entity.icon",
        "labels": "assign_labels",
        "name": "update_entity.new_name",
        "new_entity_id": "update_entity.new_entity_id",
        "device_class": "update_entity.show_as",
        "options": (
            "update_entity.settings (display settings) / update_entity.expose_to_* "
            "(voice assistants); a lock's default code is refused (entity_settings)"
        ),
        "options_domain": "update_entity.settings",
    },
    "device": {
        "area_id": "update_device.area",
        "disabled_by": "update_device.disabled",
        "labels": "assign_labels.device_ids",
        "name_by_user": "update_device.new_name",
    },
    "area": {
        "aliases": "update_area.aliases",
        "floor_id": "update_area.floor",
        "humidity_entity_id": "update_area.humidity_sensor",
        "icon": "update_area.icon",
        "labels": "assign_labels.areas",
        "name": "update_area.new_name",
        "temperature_entity_id": "update_area.temperature_sensor",
    },
    "floor": {
        "aliases": "update_floor.aliases",
        "icon": "update_floor.icon",
        "level": "update_floor.level",
        "name": "update_floor.new_name",
    },
    "label": {
        "color": "create_label.color",
        "description": "create_label.description",
        "icon": "create_label.icon",
        "name": "create_label.new_name",
    },
    "category": {"icon": "create_category.icon", "name": "create_category.new_name"},
}

# Field → why no tool sets it. Known gaps say so; they are the backlog.
_REGISTRY_LEFT_OUT: dict[str, dict[str, str]] = {
    "area": {"picture": "An uploaded image; there is no file to upload from a tool call."},
}


def _ws_fields(module: str, handler: str) -> set[str]:
    schema = getattr(getattr(importlib.import_module(module), handler), "_ws_schema")
    return {str(key) for key in getattr(schema, "schema", schema)} - {"id", "type"}


@pytest.mark.parametrize("kind", sorted(_REGISTRY_COMMANDS))
def test_every_registry_setting_has_a_tool_or_a_reason(kind: str) -> None:
    module, handler, addressing = _REGISTRY_COMMANDS[kind]
    fields = _ws_fields(module, handler) - addressing
    known = set(_REGISTRY_COVERED.get(kind, {})) | set(_REGISTRY_LEFT_OUT.get(kind, {}))

    assert not (fields - known), _NEW_OPTION.format(
        kind=kind,
        names=sorted(fields - known),
        table="_REGISTRY_COVERED / _REGISTRY_LEFT_OUT",
    )


def test_the_tools_named_as_covering_a_setting_exist() -> None:
    """The coverage table must not outlive the tools it cites."""
    for covered in _REGISTRY_COVERED.values():
        for where in covered.values():
            tool = where.split(".", 1)[0].split(" ", 1)[0]
            assert tool in TOOL_MAP, where


# ── Automations: every key of Home Assistant's schema survives our validator ──

# A valid sample of each top-level key, so the check is that the VALUE comes
# through, not merely that the name is known.
_AUTOMATION_SAMPLES: dict[str, Any] = {
    "alias": "Porch",
    "description": "Porch light at the door",
    "initial_state": True,
    "mode": "queued",
    "max": 3,
    "max_exceeded": "silent",
    "variables": {"brightness": 80},
    "trigger_variables": {"room": "porch"},
    "trace": {"stored_traces": 5},
    "triggers": [{"trigger": "state", "entity_id": "binary_sensor.door", "to": "on"}],
    "conditions": [{"condition": "state", "entity_id": "binary_sensor.door", "state": "on"}],
    "actions": [{"action": "light.turn_on", "target": {"entity_id": "light.porch"}}],
}

_AUTOMATION_LEFT_OUT: dict[str, str] = {
    "id": "Assigned by the write path (a new automation gets its own; an edit keeps its own).",
    "hide_entity": "Deprecated by Home Assistant; it has no effect.",
}


def _top_level_keys(schema: Any) -> set[str]:
    """A schema's top-level keys, through a `vol.All` or `vol.Schema` wrapper."""
    import voluptuous as vol  # noqa: PLC0415

    inner = schema
    while not isinstance(inner, dict):
        if isinstance(inner, vol.All):
            inner = next(v for v in inner.validators if isinstance(v, dict | vol.Schema))
        else:
            inner = inner.schema
    return {str(key) for key in inner}


def _automation_keys() -> set[str]:
    from homeassistant.components.automation import config  # noqa: PLC0415

    return _top_level_keys(config.PLATFORM_SCHEMA)


async def test_every_automation_option_comes_through_our_validator(hass: HomeAssistant) -> None:
    keys = _automation_keys()
    unsampled = keys - set(_AUTOMATION_SAMPLES) - set(_AUTOMATION_LEFT_OUT)
    assert not unsampled, _NEW_OPTION.format(
        kind="automation",
        names=sorted(unsampled),
        table="_AUTOMATION_SAMPLES (with a valid value) / _AUTOMATION_LEFT_OUT",
    )

    hass.states.async_set("binary_sensor.door", "off")
    hass.states.async_set("light.porch", "off")

    async def _noop(_call: Any) -> None:
        return None

    hass.services.async_register("light", "turn_on", _noop)
    payload = {key: _AUTOMATION_SAMPLES[key] for key in keys if key in _AUTOMATION_SAMPLES}

    ok, reason, normalized = validate_automation_payload(payload, hass)

    assert ok, reason
    lost = {
        key
        for key in payload
        if key not in ("triggers", "conditions", "actions")
        and (normalized or {}).get(key) != payload[key]
    }
    assert not lost, f"Our validator drops or changes automation option(s) {sorted(lost)}."


# ── Scripts: every key of Home Assistant's schema is a set_script parameter ──

_SCRIPT_PARAMS: dict[str, str] = {
    "alias": "alias",
    "sequence": "sequence",
    "description": "description",
    "icon": "icon",
    "mode": "mode",
    "fields": "fields",
    "variables": "variables",
    "max": "max",
    "max_exceeded": "max_exceeded",
}

_SCRIPT_LEFT_OUT: dict[str, str] = {
    "trace": "A debugging setting (stored trace count); kept as stored, not set.",
}


def test_every_script_option_is_a_set_script_parameter() -> None:
    from homeassistant.components.script import config  # noqa: PLC0415

    keys = _top_level_keys(config.SCRIPT_ENTITY_SCHEMA)
    unknown = keys - set(_SCRIPT_PARAMS) - set(_SCRIPT_LEFT_OUT)
    assert not unknown, _NEW_OPTION.format(
        kind="script", names=sorted(unknown), table="_SCRIPT_PARAMS / _SCRIPT_LEFT_OUT"
    )
    params = {p.name for p in TOOL_MAP["set_script"].params}
    assert set(_SCRIPT_PARAMS.values()) <= params


# ── Storage helpers: every create-schema key reaches the collection ──

_DAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


def _every_argument(domain: str) -> dict[str, Any]:
    """One typed value for every create_helper parameter."""
    samples: dict[str, Any] = {"string": "x", "number": 1, "integer": 1, "boolean": True}
    arguments = {
        p.name: samples.get(p.type, "x")
        for p in TOOL_CREATE_HELPER.params
        if p.name not in ("domain", "type", "fields", "flow_id", "remaining_intent")
    }
    arguments["options"] = ["a"]
    arguments["schedule"] = {day: [] for day in _DAYS}
    arguments["domain"] = domain
    return arguments


_HELPER_LEFT_OUT: dict[str, dict[str, str]] = {
    "person": {
        "user_id": (
            "Links a login: user ids are admin-only, and a wrong link hands one "
            "person's presence to another's account. Settings → People."
        ),
        "picture": (
            "An image uploaded on the People page; a URL set from here would be "
            "fetched by every viewer's browser."
        ),
    },
}


@pytest.mark.parametrize("domain", CREATABLE_HELPER_DOMAINS)
def test_every_helper_option_can_be_given(domain: str) -> None:
    schema = _create_schema(domain)
    assert schema is not None, f"Home Assistant's {domain} create schema could not be read."
    produced = set(create_helper_fields(_every_argument(domain)))
    if "schedule" in produced:
        produced |= set(_DAYS)

    missing = _schema_keys(schema) - produced - set(_HELPER_LEFT_OUT.get(domain, {}))
    assert not missing, _NEW_OPTION.format(
        kind=domain, names=sorted(missing), table="create_helper's parameters / _HELPER_LEFT_OUT"
    )
