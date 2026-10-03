"""Propose a storage-collection helper for the PANEL to create.

``input_boolean``, ``input_select`` and the rest of the UI-created helpers are
storage collections whose collection object is a local inside each
component's ``async_setup``. The only supported way to add one is the
component's admin-only ``<domain>/create`` websocket command, which an
in-process integration cannot call and the panel — an authenticated websocket
client — can. Same arrangement as ``create_dashboard``: this module validates
and hands back a closed intent; the panel builds the fixed websocket call from
it under the signed-in user's own account.

Validation uses each component's OWN create schema, read off its collection
class, so anything Home Assistant would reject is refused here — before the
user is shown a Create button that cannot work — and the fields the panel
sends are exactly what HA will store, which is what lets the panel recognise
its own earlier attempt on a retry.
"""

from __future__ import annotations

from datetime import timedelta
import importlib
import logging
from typing import TYPE_CHECKING, Any, Final

from homeassistant.exceptions import HomeAssistantError
import voluptuous as vol

from .helpers import sanitize_untrusted_text

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

# domain → (collection class, attribute holding its create schema). Named
# rather than discovered: the attribute is ``SCHEMA`` on input_number and
# ``CREATE_UPDATE_SCHEMA`` everywhere else, and a domain added here is one the
# panel also has to allowlist.
_COLLECTIONS: Final[dict[str, tuple[str, str]]] = {
    "input_boolean": ("InputBooleanStorageCollection", "CREATE_UPDATE_SCHEMA"),
    "input_button": ("InputButtonStorageCollection", "CREATE_UPDATE_SCHEMA"),
    "input_select": ("InputSelectStorageCollection", "CREATE_UPDATE_SCHEMA"),
    "input_number": ("NumberStorageCollection", "SCHEMA"),
    "input_text": ("InputTextStorageCollection", "CREATE_UPDATE_SCHEMA"),
    "input_datetime": ("DateTimeStorageCollection", "CREATE_UPDATE_SCHEMA"),
    "counter": ("CounterStorageCollection", "CREATE_UPDATE_SCHEMA"),
    "timer": ("TimerStorageCollection", "CREATE_UPDATE_SCHEMA"),
}

CREATABLE_HELPER_DOMAINS: Final = tuple(_COLLECTIONS)


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

    accepted = _schema_keys(schema)
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
            "label": f"Create the {sanitize_untrusted_text(name, 60)} {domain} helper",
        },
    }
