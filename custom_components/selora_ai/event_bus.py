"""Fire an event on Home Assistant's bus — for event-triggered automations,
Node-RED flows, and testing an automation by simulating what triggers it.

* **Home Assistant's own events are refused.** ``state_changed``,
  ``homeassistant_stop``, ``call_service``, the registry updates and the rest
  are how the system tells its parts what happened; firing one by hand tells
  them something that did not happen.
* **It asks first, naming what it starts.** An event can trigger anything an
  automation does — a custom button event can unlock a door — so the
  automations listening for it are named and the call needs ``confirmed``,
  unless the install turned the approval requirement off, as service calls do.
  Other subscribers (Node-RED, custom integrations) cannot be seen, which the
  confirmation says.
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any, Final

from .helpers import sanitize_untrusted_text

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_MAX_DATA_CHARS: Final = 16000
_TYPE_RE: Final = re.compile(r"[^\s\x00-\x1f]{1,64}")
# Beyond core's EVENT_* constants and the patterns in `_internal`: system
# events of components, by name. Device events (zha_event, a button's own
# event) stay allowed — simulating one is what testing an automation needs.
_SYSTEM_EVENTS: Final = frozenset(
    {
        "automation_triggered",
        "script_started",
        "data_entry_flow_progressed",
        "user_added",
        "user_removed",
        "user_updated",
        "persistent_notifications_updated",
        "config_entry_discovered",
    }
)


def _internal(event_type: str) -> bool:
    import homeassistant.const as const  # noqa: PLC0415

    core_events = {
        value
        for name, value in vars(const).items()
        if name.startswith("EVENT_") and isinstance(value, str)
    }
    return (
        event_type in core_events
        or event_type in _SYSTEM_EVENTS
        or event_type.endswith(("_registry_updated", "_reloaded"))
        or event_type.startswith("homeassistant_")
    )


def _listens(trigger: Any, event_type: str) -> bool:
    if not isinstance(trigger, dict):
        return False
    if (trigger.get("trigger") or trigger.get("platform")) != "event":
        return False
    wanted = trigger.get("event_type")
    return event_type in (wanted if isinstance(wanted, list) else [wanted])


def _listening_automations(hass: HomeAssistant, event_type: str) -> list[dict[str, str]]:
    """Automations with an event trigger for *event_type*."""
    component = hass.data.get("automation")
    found = []
    for entity in getattr(component, "entities", ()):
        config = getattr(entity, "raw_config", None) or {}
        triggers = config.get("triggers", config.get("trigger")) or []
        if not isinstance(triggers, list):
            triggers = [triggers]
        if any(_listens(trigger, event_type) for trigger in triggers):
            found.append(
                {
                    "entity_id": entity.entity_id,
                    "name": sanitize_untrusted_text(getattr(entity, "name", "") or "", 80),
                }
            )
    return sorted(found, key=lambda row: row["entity_id"])


async def async_fire_event(
    hass: HomeAssistant, event_type: str, data: Any = None, *, confirmed: bool = False
) -> dict[str, Any]:
    """Fire *event_type* with *data*, once confirmed."""
    from .command_policy_options import resolve_command_policy_options  # noqa: PLC0415

    event_type = str(event_type or "").strip()
    if not _TYPE_RE.fullmatch(event_type):
        return {"error": "event_type is 1 to 64 characters, with no spaces."}
    if _internal(event_type):
        return {
            "error": (
                f"'{sanitize_untrusted_text(event_type, 64)}' is one of Home Assistant's "
                "own events; firing it by hand reports something that did not happen."
            )
        }
    if data is None:
        data = {}
    if not isinstance(data, dict):
        return {"error": "data is an object, delivered as the event's data."}
    try:
        encoded = json.dumps(data)
    except (TypeError, ValueError):
        return {"error": "data must be plain JSON."}
    if len(encoded) > _MAX_DATA_CHARS:
        return {"error": f"data is larger than {_MAX_DATA_CHARS} characters."}

    listening = _listening_automations(hass, event_type)
    if not confirmed and resolve_command_policy_options(hass).approval_required:
        return {
            "requires_confirmation": True,
            "event_type": event_type,
            "automations_triggered": listening,
            "hint": (
                (
                    f"This starts {len(listening)} automation(s): "
                    + ", ".join(row["name"] or row["entity_id"] for row in listening)
                    + ". "
                    if listening
                    else "No automation listens for it. "
                )
                + "Anything else subscribed (Node-RED, an integration) cannot be seen "
                "from here. Tell the user, and only once they agree call again with "
                "confirmed=true."
            ),
        }

    hass.bus.async_fire(event_type, json.loads(encoded))
    return {"status": "fired", "event_type": event_type, "automations_triggered": listening}
