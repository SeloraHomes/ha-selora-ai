"""Create a config-entry helper by driving its integration's own config flow.

Home Assistant's UI helpers come in two kinds. The storage-collection ones
(``input_boolean``, ``timer`` …) are created through a websocket command only
the panel can call (``helper_manager``). The rest — template entities of every
type, utility meters, thresholds, derivatives, min/max, times of day … — are
config entries created by a config flow, which runs in-process. This drives
that flow generically, the way ``group_manager`` drives ``group``'s, so one
tool covers every helper HA ships instead of one tool per use case.

The flow is the schema: a call without ``options`` returns the fields the
flow's form asks for, and a call with them submits the form, so HA's own
validation decides what is accepted. Only integrations that declare
``integration_type: helper`` are driven — a config flow for a device or a
cloud account is not something a chat should be completing.
"""

from __future__ import annotations

import contextlib
import logging
from typing import TYPE_CHECKING, Any, Final

from homeassistant.data_entry_flow import AbortFlow, InvalidData, UnknownFlow, UnknownStep
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_registry as er
from homeassistant.loader import IntegrationNotFound, async_get_integration
import voluptuous as vol

from .helpers import sanitize_untrusted_text

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

# Helper integrations with a tool of their own, which polices what the generic
# flow cannot (member domains, hidden members, numeric members).
_DEDICATED: Final[dict[str, str]] = {"group": "create_group"}

_FLOW_ERRORS = (AbortFlow, UnknownFlow, UnknownStep, vol.Invalid, ValueError, KeyError)

# Field keys kept when describing a form. The rest of the serialized selector
# is UI detail the caller does not need to fill the field.
_FIELD_KEYS: Final = ("name", "required", "default", "selector", "options", "type")


def _to_field_list(schema: vol.Schema) -> Any:
    """Serialize a form schema with the library ``cv.custom_serializer`` pairs with.

    Home Assistant 2026.9 moved form serialization from ``voluptuous_serialize``
    to ``probatio`` and dropped the former from its requirements. The custom
    serializer answers "unsupported" with ITS library's sentinel, which the
    other library does not recognise and returns in place of the field list —
    so the serializer has to be the one ``cv`` itself imports, not whichever
    happens to be installed. 2026.9 imports ``to_field_list`` into ``cv``;
    2026.10 imports the ``probatio`` module instead. And
    ``voluptuous_serialize`` is imported only on a core that still uses it,
    since a 2026.9+ install need not have it at all.
    """
    to_field_list = getattr(cv, "to_field_list", None) or getattr(
        getattr(cv, "probatio", None), "to_field_list", None
    )
    if to_field_list is not None:
        return to_field_list(schema, custom_serializer=cv.custom_serializer)
    import voluptuous_serialize  # noqa: PLC0415

    return voluptuous_serialize.convert(schema, custom_serializer=cv.custom_serializer)


def _describe_fields(schema: vol.Schema | None) -> list[dict[str, Any]]:
    if schema is None:
        return []
    try:
        fields = _to_field_list(schema)
    except (TypeError, ValueError, vol.Invalid) as exc:
        _LOGGER.debug("Could not describe helper form: %s", exc)
        return []
    if not isinstance(fields, list):
        _LOGGER.debug("Could not describe helper form: serializer returned %r", fields)
        return []
    described = []
    for field in fields:
        entry = {k: field[k] for k in _FIELD_KEYS if k in field}
        selector = entry.get("selector")
        if isinstance(selector, dict):
            # {"select": {"options": [...], "mode": ...}} → keep the kind and
            # its choices; the rest is presentation.
            kind = next(iter(selector), None)
            config = selector.get(kind) if kind else None
            entry["selector"] = kind
            if isinstance(config, dict) and isinstance(config.get("options"), list):
                entry["options"] = [
                    o.get("value") if isinstance(o, dict) else o for o in config["options"]
                ]
        described.append(entry)
    return described


async def _is_helper_integration(hass: HomeAssistant, domain: str) -> bool:
    try:
        integration = await async_get_integration(hass, domain)
    except IntegrationNotFound:
        return False
    return integration.integration_type == "helper" and bool(integration.config_flow)


def _abort(hass: HomeAssistant, flow_id: str | None) -> None:
    if flow_id:
        with contextlib.suppress(UnknownFlow):
            hass.config_entries.flow.async_abort(flow_id)


async def async_create_flow_helper(
    hass: HomeAssistant,
    integration: str,
    kind: str | None,
    options: dict[str, Any] | None,
) -> dict[str, Any]:
    """Create a config-entry helper, or describe what its form needs."""
    integration = str(integration or "").strip().lower()
    if integration in _DEDICATED:
        return {"error": f"Use {_DEDICATED[integration]} for a {integration} helper."}
    if not await _is_helper_integration(hass, integration):
        return {
            "error": (
                f"'{sanitize_untrusted_text(integration, 40)}' is not a helper integration "
                "with a config flow. Use a storage helper domain (input_boolean, "
                "input_select, …) or a helper integration such as template, "
                "utility_meter, threshold or derivative."
            )
        }

    flow_id: str | None = None
    succeeded = False
    try:
        result = await hass.config_entries.flow.async_init(integration, context={"source": "user"})
        flow_id = result.get("flow_id")
        if result.get("type") == "menu":
            choices = [str(o) for o in (result.get("menu_options") or [])]
            if not kind or kind not in choices:
                return {
                    "status": "needs_type",
                    "integration": integration,
                    "types": choices,
                    "hint": "Call again with `type` set to one of these.",
                }
            result = await hass.config_entries.flow.async_configure(flow_id, {"next_step_id": kind})
        if result.get("type") != "form":
            return {"error": f"The {integration} flow did not present a form."}
        fields = _describe_fields(result.get("data_schema"))
        if not options:
            return {
                "status": "needs_options",
                "integration": integration,
                "type": kind,
                "fields": fields,
                "hint": "Call again with `options` holding these fields.",
            }
        try:
            result = await hass.config_entries.flow.async_configure(flow_id, options)
        except InvalidData as exc:
            return {
                "error": f"Home Assistant rejected those options: {exc.schema_errors or exc}",
                "fields": fields,
            }
        if result.get("type") == "form":
            # Either the same form with errors, or a further step this generic
            # driver does not walk.
            return {
                "error": (
                    f"The {integration} flow wants more: "
                    f"{result.get('errors') or result.get('step_id')}"
                ),
                "fields": _describe_fields(result.get("data_schema")),
            }
        if result.get("type") != "create_entry":
            reason = result.get("reason") or result.get("type")
            return {
                "error": f"The helper was not created ({sanitize_untrusted_text(str(reason))})."
            }
        succeeded = True
    except _FLOW_ERRORS as exc:
        _LOGGER.warning("%s helper flow failed: %s", integration, exc)
        return {"error": f"Home Assistant rejected the helper: {exc}"}
    finally:
        if not succeeded:
            _abort(hass, flow_id)

    entry = result["result"]
    await hass.async_block_till_done()
    entity_ids = [
        e.entity_id for e in er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
    ]
    _LOGGER.info("Created %s helper '%s' (%s)", integration, entry.title, entity_ids)
    return {
        "status": "created",
        "integration": integration,
        "type": kind,
        "title": entry.title,
        "entity_ids": entity_ids,
    }
