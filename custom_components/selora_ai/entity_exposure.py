"""Which voice assistants can see an entity, read and changed.

Home Assistant keeps one exposure setting per entity per assistant: Assist
(``conversation``), Alexa (``cloud.alexa``) and Google (``cloud.google_assistant``).
Selora's own Alexa skill reads the ``cloud.alexa`` setting too (see
``alexa_config.ALEXA_ASSISTANT``), so one toggle serves both Alexa skills.

* **Only an assistant this hub uses can be changed.** Home Assistant shows the
  Alexa and Google columns only to a Home Assistant Cloud account; without one,
  a change there is a setting nobody can see or undo. Alexa counts as in use
  when either skill is linked, Google only with Home Assistant Cloud.
* **Reading never writes.** Core's ``async_should_expose`` records the default
  it computes into the entity's options, pinning it for good (the trap
  ``alexa_config`` documents). A read here takes the recorded setting, or works
  out the default without recording it.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Final

from homeassistant.exceptions import HomeAssistantError

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

ASSISTANTS: Final[dict[str, str]] = {
    "assist": "conversation",
    "alexa": "cloud.alexa",
    "google": "cloud.google_assistant",
}


def _cloud_logged_in(hass: HomeAssistant) -> bool:
    return bool(getattr(hass.data.get("cloud"), "is_logged_in", False))


def _selora_alexa_linked(hass: HomeAssistant) -> bool:
    from . import _alexa_credentials  # noqa: PLC0415

    return _alexa_credentials(hass) is not None


def assistants_in_use(hass: HomeAssistant) -> list[str]:
    """The assistants whose exposure means something on this hub."""
    names = ["assist"]
    cloud = _cloud_logged_in(hass)
    if cloud or _selora_alexa_linked(hass):
        names.append("alexa")
    if cloud:
        names.append("google")
    return names


def unavailable(hass: HomeAssistant, requested: dict[str, bool]) -> str | None:
    """Why a requested change cannot be made, or None."""
    in_use = assistants_in_use(hass)
    missing = [name for name in requested if name not in in_use]
    if not missing:
        return None
    why = {
        "alexa": "no Alexa skill is linked (neither Selora's nor Home Assistant Cloud's)",
        "google": "Google Assistant needs Home Assistant Cloud, which is not signed in",
    }
    return "Nothing was changed: " + "; ".join(why[name] for name in missing) + "."


def async_set_exposure(hass: HomeAssistant, entity_id: str, requested: dict[str, bool]) -> None:
    from homeassistant.components.homeassistant.exposed_entities import (  # noqa: PLC0415
        async_expose_entity,
    )

    for name, expose in requested.items():
        async_expose_entity(hass, ASSISTANTS[name], entity_id, expose)


def async_get_exposure(hass: HomeAssistant, entity_id: str) -> dict[str, bool] | None:
    """Each in-use assistant's answer for this entity, without recording any."""
    from homeassistant.components.homeassistant.const import (  # noqa: PLC0415
        DATA_EXPOSED_ENTITIES,
    )
    from homeassistant.helpers import entity_registry as er  # noqa: PLC0415

    exposed = hass.data.get(DATA_EXPOSED_ENTITIES)
    if exposed is None:
        return None
    try:
        settings = exposed.async_get_entity_settings(entity_id)
    except HomeAssistantError:
        settings = {}
    entry = er.async_get(hass).async_get(entity_id)
    result: dict[str, bool] = {}
    for name in assistants_in_use(hass):
        key = ASSISTANTS[name]
        recorded: Any = (settings.get(key) or {}).get("should_expose")
        if recorded is not None:
            result[name] = bool(recorded)
            continue
        default = _default(exposed, key, entity_id, entry)
        if default is not None:
            result[name] = default
    return result


def _default(exposed: Any, key: str, entity_id: str, entry: Any) -> bool | None:
    """What core would decide, from its own rule; None if core moved it."""
    if not exposed.async_get_expose_new_entities(key):
        return False
    rule = getattr(exposed, "_is_default_exposed", None)
    if rule is None:
        return None
    try:
        return bool(rule(entity_id, entry))
    except (HomeAssistantError, TypeError):
        _LOGGER.debug("Could not work out the default exposure of %s", entity_id)
        return None
