"""Create storage-collection helpers: proposed for the panel, or directly.

``input_boolean``, ``input_select`` and the rest of the UI-created helpers are
storage collections whose collection object is a local inside each
component's ``async_setup``, published only through its admin-only
``<domain>/create`` websocket command. Same arrangement as dashboards: chat
validates and hands back a closed intent, and the panel builds the fixed
websocket call from it under the signed-in user's own account; MCP, which has
no panel, creates through the collection recovered from that command
(``helpers.registered_storage_collection``) after the same validation.

Validation uses each component's OWN create schema, read off its collection
class, so anything Home Assistant would reject is refused here — before the
user is shown a Create button that cannot work — and the fields the panel
sends are exactly what HA will store, which is what lets the panel recognise
its own earlier attempt on a retry.
"""

from __future__ import annotations

from datetime import timedelta
import importlib
import json
import logging
from typing import TYPE_CHECKING, Any, Final

from homeassistant.exceptions import HomeAssistantError
import voluptuous as vol

from .helpers import (
    attach_previous,
    registered_storage_collection,
    sanitize_untrusted_text,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

# domain → (collection class, attribute holding its create schema). Named
# rather than discovered: the attribute is ``SCHEMA`` on input_number,
# ``CREATE_SCHEMA`` on zone and person, and ``CREATE_UPDATE_SCHEMA`` everywhere else, and a
# domain added here is one the panel also has to allowlist. A zone or a person
# is not a helper to the user, but it is the same kind of storage collection.
_COLLECTIONS: Final[dict[str, tuple[str, str]]] = {
    "input_boolean": ("InputBooleanStorageCollection", "CREATE_UPDATE_SCHEMA"),
    "input_button": ("InputButtonStorageCollection", "CREATE_UPDATE_SCHEMA"),
    "input_select": ("InputSelectStorageCollection", "CREATE_UPDATE_SCHEMA"),
    "input_number": ("NumberStorageCollection", "SCHEMA"),
    "input_text": ("InputTextStorageCollection", "CREATE_UPDATE_SCHEMA"),
    "input_datetime": ("DateTimeStorageCollection", "CREATE_UPDATE_SCHEMA"),
    "counter": ("CounterStorageCollection", "CREATE_UPDATE_SCHEMA"),
    "timer": ("TimerStorageCollection", "CREATE_UPDATE_SCHEMA"),
    "zone": ("ZoneStorageCollection", "CREATE_SCHEMA"),
    "schedule": ("ScheduleStorageCollection", "SCHEMA"),
    "person": ("PersonStorageCollection", "CREATE_SCHEMA"),
}

# Fields of a collection this module never sets. A person's ``user_id`` links a
# login account: the ids are admin-only, and a wrong link hands one person's
# presence to another's account, so it stays in Settings → People.
_NOT_SET_HERE: Final[dict[str, frozenset[str]]] = {"person": frozenset({"user_id"})}

CREATABLE_HELPER_DOMAINS: Final = tuple(_COLLECTIONS)

_DAYS: Final = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


def _expand_schedule(fields: dict[str, Any]) -> str | None:
    """Spread a ``schedule`` argument (``{day: [{from, to}, …]}``) into the
    per-day keys a schedule stores, in place; an error, or None.

    One object rather than seven parameters keeps the tool schema small. A day
    named wrongly is refused rather than dropped as unknown fields are: dropped,
    "mon" would save a schedule that is never on, and say it was created.
    """
    if "schedule" not in fields:
        return None
    week = fields.pop("schedule")
    if not isinstance(week, dict):
        return "schedule is an object of days, e.g. {'monday': [{'from': '07:00', 'to': '09:00'}]}."
    for key, blocks in week.items():
        name = str(key).strip().lower()
        day = next((d for d in _DAYS if d == name or d[:3] == name), None)
        if day is None:
            return f"'{sanitize_untrusted_text(key, 20)}' is not a day; use monday … sunday."
        fields[day] = blocks if isinstance(blocks, list) else [blocks]
    return None


def _create_schema(domain: str) -> vol.Schema | None:
    """The component's own create schema, or None when it cannot be read."""
    class_name, attribute = _COLLECTIONS[domain]
    try:
        module = importlib.import_module(f"homeassistant.components.{domain}")
    except ImportError:
        return None
    schema = getattr(getattr(module, class_name, None), attribute, None)
    return schema if isinstance(schema, vol.Schema) else None


def _schema_keys(schema: vol.Schema) -> set[str]:
    """Field names the schema accepts — the outer dict of a ``vol.All`` too."""
    inner: Any = schema.schema
    if isinstance(inner, vol.All):
        inner = next((v for v in inner.validators if isinstance(v, dict)), {})
    if isinstance(inner, vol.Schema):
        inner = inner.schema
    return {str(key) for key in inner} if isinstance(inner, dict) else set()


def _noun(domain: str) -> str:
    """What the user calls one: a zone and a person are not helpers to them."""
    return domain if domain in ("zone", "person") else f"{domain} helper"


def _unknown_trackers(hass: HomeAssistant, fields: dict[str, Any]) -> str | None:
    """A person's device trackers that do not exist, as an error, or None.

    The schema checks only the domain, so a mistyped tracker is stored and the
    person never shows as home.
    """
    from homeassistant.helpers import entity_registry as er  # noqa: PLC0415

    registry = er.async_get(hass)
    missing = [
        str(tracker)
        for tracker in fields.get("device_trackers") or ()
        if hass.states.get(str(tracker)) is None and registry.async_get(str(tracker)) is None
    ]
    if not missing:
        return None
    shown = ", ".join(sanitize_untrusted_text(m, 80) for m in missing[:5])
    return f"No device tracker {shown} exists. search_entities(domain='device_tracker') lists them."


def _format_duration(delta: timedelta) -> str:
    """``H:MM:SS``, the form the timer collection stores a duration in."""
    total = int(delta.total_seconds())
    hours, remainder = divmod(total, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours}:{minutes:02}:{seconds:02}"


def _jsonable(fields: dict[str, Any]) -> dict[str, Any]:
    """The validated fields in a form that survives the trip to the panel."""
    return {
        key: _format_duration(value) if isinstance(value, timedelta) else value
        for key, value in fields.items()
    }


def _existing_helper(hass: HomeAssistant, domain: str, name: str) -> str | None:
    """entity_id of a helper in *domain* already called *name*, if any.

    By name, case- and whitespace-insensitively, the way the user reads it.
    HA would not refuse the duplicate — the collection suffixes the id — so
    left alone the user ends up with two "Alarm mode" helpers and an
    automation wired to whichever one the model happened to name.
    """
    wanted = " ".join(name.split()).casefold()
    # By NAME only. A renamed helper keeps its old slug — "Vacation mode" can
    # still be input_boolean.guest_mode — and HA suffixes a new one's id
    # rather than refusing, so a free name on a taken slug is not a duplicate.
    for state in hass.states.async_all(domain):
        friendly = str(state.attributes.get("friendly_name") or "")
        if " ".join(friendly.split()).casefold() == wanted:
            return state.entity_id
    return None


async def async_propose_helper(
    hass: HomeAssistant,
    domain: str,
    fields: dict[str, Any],
) -> dict[str, Any]:
    """Validate a new-helper request and hand back a closed intent.

    Creates nothing. *fields* are the caller's arguments in the component's
    own vocabulary; any the domain does not take are dropped rather than
    refused — ``options`` on a toggle is not something a user can mean, and
    refusing it would only cost a round to say so.
    """
    domain = str(domain or "").strip().lower()
    if domain not in _COLLECTIONS:
        return {
            "error": (
                f"'{sanitize_untrusted_text(domain, 40)}' is not a helper type that can be "
                f"created here. Supported: {', '.join(CREATABLE_HELPER_DOMAINS)}."
            )
        }
    # The websocket command only exists once the component is set up, so a
    # button for an unloaded one fails every time it is pressed.
    if domain not in hass.config.components:
        return {
            "error": (
                f"The {domain} integration is not loaded in this Home Assistant, so "
                f"its helpers cannot be created. It is part of default_config."
            )
        }
    schema = _create_schema(domain)
    if schema is None:
        return {"error": f"Could not read Home Assistant's {domain} schema."}

    fields = dict(fields)
    if error := _expand_schedule(fields):
        return {"error": error}
    accepted = _schema_keys(schema) - _NOT_SET_HERE.get(domain, frozenset())
    supplied = {k: v for k, v in fields.items() if v is not None}
    dropped = sorted(set(supplied) - accepted)
    if dropped:
        _LOGGER.debug("create_helper: %s takes no %s; dropped", domain, ", ".join(dropped))
    candidate = {k: v for k, v in supplied.items() if k in accepted}

    try:
        validated = schema(candidate)
    except (vol.Invalid, HomeAssistantError) as exc:
        return {
            "error": (
                f"Home Assistant would refuse that {domain}: "
                f"{sanitize_untrusted_text(str(exc), 200)}"
            )
        }

    if error := _unknown_trackers(hass, validated):
        return {"error": error}
    name = str(validated.get("name") or "").strip()
    if not name:
        return {"error": "A helper name is required."}
    validated["name"] = name

    existing = _existing_helper(hass, domain, name)
    if existing is not None:
        return {
            "error": (
                f"A {domain} named '{sanitize_untrusted_text(name, 60)}' already exists "
                f"as {existing}. Use it, or pick a different name."
            )
        }

    return {
        "requires_approval": True,
        "client_action": {
            # Every value here passed HA's own schema. The panel copies only the
            # keys it allowlists for this domain into `<domain>/create`; it
            # never forwards the descriptor itself.
            "kind": "create_helper",
            "domain": domain,
            "name": name,
            "fields": _jsonable(dict(validated)),
            "label": f"Create the {sanitize_untrusted_text(name, 60)} {_noun(domain)}",
        },
    }


def _helper_collection(hass: HomeAssistant, domain: str) -> Any | None:
    """The component's storage collection, or None when it cannot be reached."""
    class_name, _ = _COLLECTIONS[domain]
    try:
        module = importlib.import_module(f"homeassistant.components.{domain}")
    except ImportError:
        return None
    expected = getattr(module, class_name, None)
    if not isinstance(expected, type):
        return None
    return registered_storage_collection(hass, f"{domain}/create", expected)


async def async_create_helper(
    hass: HomeAssistant,
    domain: str,
    fields: dict[str, Any],
) -> dict[str, Any]:
    """Create a storage helper on the spot — for callers with no panel (MCP).

    Validated exactly as the proposal is — the same refusals, a name in use
    included — then created through the component's own collection, which
    re-applies its schema and registers the entity.
    """
    from homeassistant.helpers import entity_registry as er  # noqa: PLC0415

    proposal = await async_propose_helper(hass, domain, fields)
    if "error" in proposal:
        return proposal
    intent = proposal["client_action"]
    domain = intent["domain"]

    collection = _helper_collection(hass, domain)
    if collection is None:
        return {
            "error": (
                f"This Home Assistant version does not let Selora reach its {domain} "
                "helpers, so it has to be created under Settings → Devices & "
                "services → Helpers."
            )
        }
    try:
        item = await collection.async_create_item(dict(intent["fields"]))
    except (vol.Invalid, HomeAssistantError, ValueError) as exc:
        return {
            "error": (
                f"Home Assistant refused that {domain}: {sanitize_untrusted_text(str(exc), 200)}"
            )
        }

    # The collection suffixes the id on a clash, so the name does not give
    # the entity_id; the registry does, keyed by the item id.
    entity_id = er.async_get(hass).async_get_entity_id(domain, domain, str(item.get("id")))
    return {
        "status": "created",
        "domain": domain,
        "name": intent["name"],
        **({"entity_id": entity_id} if entity_id else {}),
    }


def _resolve_storage_helper(
    hass: HomeAssistant, entity_id: str
) -> tuple[str, Any, dict[str, Any]] | str:
    """``(domain, collection, stored item)`` for a UI-created helper, or why not.

    The item is found through the entity registry: a storage helper's entity
    is registered under its own domain as platform with the item id as
    unique_id, and the collection suffixes that id on a clash, so the name
    does not give it. A helper defined in YAML has no item in the storage
    collection, and a config-entry helper (template, utility_meter …) is not
    in one at all; each is refused with where to change it.
    """
    from homeassistant.helpers import entity_registry as er  # noqa: PLC0415

    entity_id = str(entity_id or "").strip().lower()
    domain = entity_id.split(".", 1)[0]
    shown = sanitize_untrusted_text(entity_id, 80)
    if entity_id == "zone.home":
        # Not a stored zone: Home Assistant draws it from the home's own
        # location and radius.
        return "zone.home is the home's own location. Change it under Settings → System → General."
    entry = er.async_get(hass).async_get(entity_id)
    if domain not in _COLLECTIONS:
        if entry is not None and entry.config_entry_id:
            return (
                f"{shown} is a helper set up as an integration. Change or remove it "
                "under Settings → Devices & services → Helpers."
            )
        return (
            f"'{shown}' is not a helper that can be changed here. Supported: "
            f"{', '.join(CREATABLE_HELPER_DOMAINS)}."
        )
    if hass.states.get(entity_id) is None and entry is None:
        return f"No helper {shown} exists. Call list_helpers for the right entity_id."
    collection = _helper_collection(hass, domain)
    if collection is None:
        return (
            f"This Home Assistant version does not let Selora reach its {domain} "
            "helpers. Change it under Settings → Devices & services → Helpers."
        )
    item = (
        collection.data.get(entry.unique_id)
        if entry is not None and entry.platform == domain
        else None
    )
    if not isinstance(item, dict):
        return (
            f"{shown} is defined in configuration.yaml, so Home Assistant does not "
            "let anything change it from the UI. It has to be edited there."
        )
    return domain, collection, item


def helper_fingerprint(item: dict[str, Any]) -> str:
    """Content hash of a stored helper — its identity on a confirmation card.

    The item id is derived from the name, so a helper deleted and recreated
    under that name answers to the same entity_id. What the user saw on the
    card is what may be deleted, so the card carries this and the delete
    re-checks it.
    """
    import hashlib  # noqa: PLC0415
    import json  # noqa: PLC0415

    payload = json.dumps(item, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


async def async_helper_dependents(hass: HomeAssistant, entity_id: str) -> list[str]:
    """What stops working when this helper goes, as countable phrases.

    Home Assistant rewrites no references, so an automation testing a deleted
    toggle fails silently. An unreadable dashboard is named as unknown rather
    than counted as clean.
    """
    from .group_manager import group_dependents  # noqa: PLC0415
    from .recipes.dashboard import async_dashboards_with_entity  # noqa: PLC0415

    refs = group_dependents(hass, entity_id)
    phrases = [
        f"{len(refs[kind])} {kind[:-1] if len(refs[kind]) == 1 else kind}"
        for kind in ("automations", "scripts", "scenes", "groups")
        if refs[kind]
    ]
    dashboards, unreadable = await async_dashboards_with_entity(hass, entity_id)
    if dashboards:
        phrases.append(f"{len(dashboards)} dashboard{'s' if len(dashboards) != 1 else ''}")
    if unreadable:
        phrases.append(
            f"{len(unreadable)} unreadable dashboard{'s' if len(unreadable) != 1 else ''}"
        )
    return phrases


async def async_update_helper(
    hass: HomeAssistant,
    entity_id: str,
    fields: dict[str, Any],
    clear: list[str] | None = None,
) -> dict[str, Any]:
    """Change a UI-created helper's settings, keeping every one not named.

    Home Assistant's helper update REPLACES the stored item — ``_update_data``
    returns the id plus the validated update, nothing else — so passing only
    the changed fields would wipe the rest (a dropdown losing its icon, a
    number its unit). The stored item is merged with the change and the whole
    validated with the component's own schema first. ``clear`` removes an
    optional setting, since a blank value reads as "not set".
    """
    resolved = _resolve_storage_helper(hass, entity_id)
    if isinstance(resolved, str):
        return {"error": resolved}
    domain, collection, item = resolved
    schema = _create_schema(domain)
    if schema is None:
        return {"error": f"Could not read Home Assistant's {domain} schema."}
    accepted = _schema_keys(schema)
    fields = dict(fields)
    if error := _expand_schedule(fields):
        return {"error": error}

    to_clear = sorted(set(clear or ()))
    if reserved := sorted(
        (set(to_clear) | {k for k, v in fields.items() if v is not None})
        & _NOT_SET_HERE.get(domain, frozenset())
    ):
        return {
            "error": (
                f"A {domain}'s {', '.join(reserved)} is changed under Settings → People, not here."
            )
        }
    unknown = [key for key in to_clear if key not in accepted]
    if unknown:
        return {"error": f"A {domain} has no {', '.join(unknown)} setting to clear."}
    supplied = {k: v for k, v in fields.items() if v is not None and k in accepted}
    if not supplied and not to_clear:
        return {"error": f"Nothing to change. Pass a {domain} setting, or clear=[…]."}
    both = sorted(set(supplied) & set(to_clear))
    if both:
        return {"error": f"Pass either a new {', '.join(both)} or clear it, not both."}

    if "name" in supplied:
        name = str(supplied["name"]).strip()
        other = _existing_helper(hass, domain, name)
        if other is not None and other != entity_id.strip().lower():
            return {
                "error": (
                    f"A {domain} named '{sanitize_untrusted_text(name, 60)}' already "
                    f"exists as {other}. Pick a different name."
                )
            }
        supplied["name"] = name

    merged = {k: v for k, v in item.items() if k != "id" and k not in to_clear}
    merged.update(supplied)
    try:
        validated = schema(merged)
    except (vol.Invalid, HomeAssistantError) as exc:
        return {
            "error": (
                f"Home Assistant would refuse that {domain}: "
                f"{sanitize_untrusted_text(str(exc), 200)}"
            )
        }
    if "device_trackers" in supplied and (error := _unknown_trackers(hass, supplied)):
        return {"error": error}
    try:
        updated = await collection.async_update_item(item["id"], _jsonable(dict(validated)))
    except (vol.Invalid, HomeAssistantError, ValueError) as exc:
        return {
            "error": (
                f"Home Assistant refused that {domain}: {sanitize_untrusted_text(str(exc), 200)}"
            )
        }
    return attach_previous(
        {
            "status": "updated",
            "entity_id": entity_id.strip().lower(),
            "name": sanitize_untrusted_text(str(updated.get("name") or ""), 60),
            "changed": sorted({*supplied, *to_clear}),
        },
        _changed_settings(
            _settings(domain, item, added=[k for k in supplied if item.get(k) is None]),
            domain,
            {*supplied, *to_clear},
        ),
        what="old settings",
    )


async def async_preview_helper_delete(hass: HomeAssistant, entity_id: str) -> dict[str, Any]:
    """Resolve a helper deletion for a confirmation card, deleting nothing."""
    resolved = _resolve_storage_helper(hass, entity_id)
    if isinstance(resolved, str):
        return {"error": resolved}
    domain, _, item = resolved
    entity_id = entity_id.strip().lower()
    name = sanitize_untrusted_text(str(item.get("name") or entity_id), 60)
    label = f"Delete the {name} {domain if domain in ('zone', 'person') else 'helper'}"
    if dependents := await async_helper_dependents(hass, entity_id):
        label = f"{label} — used by {', '.join(dependents)}"
    return {
        "requires_approval": True,
        "delete": {
            "kind": "helper",
            "target_id": entity_id,
            "entity_id": entity_id,
            "name": name,
            "label": label,
            "fingerprint": helper_fingerprint(item),
        },
    }


async def async_delete_helper(
    hass: HomeAssistant,
    entity_id: str,
    *,
    expected_fingerprint: str | None = None,
) -> dict[str, Any]:
    """Delete a UI-created helper, naming what used it.

    With *expected_fingerprint* (a confirmation card) the stored item must
    still be the one the user saw. Resolving, checking and the delete's first
    step run with no await between them, so nothing can change in between.
    """
    from homeassistant.helpers.collection import ItemNotFound  # noqa: PLC0415

    entity_id = str(entity_id or "").strip().lower()
    dependents = await async_helper_dependents(hass, entity_id)

    resolved = _resolve_storage_helper(hass, entity_id)
    if isinstance(resolved, str):
        return {"error": resolved}
    domain, collection, item = resolved
    if expected_fingerprint and helper_fingerprint(item) != expected_fingerprint:
        return {
            "error": (
                "That helper has changed since it was shown for confirmation, so it "
                "was not deleted. Check it and ask again."
            )
        }
    try:
        await collection.async_delete_item(item["id"])
    except ItemNotFound:
        return {"error": "That helper no longer exists."}
    return attach_previous(
        {
            "status": "deleted",
            "entity_id": entity_id,
            "name": sanitize_untrusted_text(str(item.get("name") or ""), 60),
            # Home Assistant rewrites no references; these now point at nothing.
            **({"was_used_by": dependents} if dependents else {}),
            **(
                {
                    "note": (
                        "Made again from previous, this person has no login link "
                        "or picture: set those under Settings → People."
                    )
                }
                if item.get("user_id") or item.get("picture")
                else {}
            ),
        },
        _settings(domain, item),
        what="deleted helper",
    )


def _changed_settings(settings: dict[str, Any], domain: str, changed: set[str]) -> dict[str, Any]:
    """Only the settings an update changed (plus its ``clear``): the rest passed
    back would be re-checked, and a tracker since removed would refuse the undo."""
    from .tool_executor import _COUNTER_SPELLING  # noqa: PLC0415

    spelled = {stored: alias for alias, stored in _COUNTER_SPELLING.items()}
    names = {
        "schedule" if key in _DAYS else spelled.get(key, key) if domain == "counter" else key
        for key in changed
    }
    return {k: v for k, v in settings.items() if k in names or k == "clear"}


def _settings(
    domain: str, item: dict[str, Any], *, added: list[str] | None = None
) -> dict[str, Any]:
    """A stored helper as the arguments ``create_helper``/``update_helper``
    take, in plain JSON types: a counter's bounds as ``min``/``max``, a
    schedule's days under ``schedule``. *added* are settings an update
    introduced, which only ``clear`` takes away again."""
    from .tool_executor import _COUNTER_SPELLING  # noqa: PLC0415

    settings: dict[str, Any] = json.loads(
        json.dumps(_jsonable({k: v for k, v in item.items() if k != "id"}), default=str)
    )
    if domain == "counter":
        for alias, stored in _COUNTER_SPELLING.items():
            if stored in settings:
                settings[alias] = settings.pop(stored)
    # A person's login link and picture are set only under Settings → People,
    # so no tool takes them back.
    settings.pop("user_id", None)
    settings.pop("picture", None)
    if domain == "schedule":
        # Empty days kept: one a later edit fills is set back by its [].
        week = {day: settings.pop(day) for day in _DAYS if day in settings}
        if week:
            settings["schedule"] = week
    if added:
        spelled = {stored: alias for alias, stored in _COUNTER_SPELLING.items()}
        settings["clear"] = sorted(
            spelled.get(key, key) if domain == "counter" else key for key in added
        )
    return settings
