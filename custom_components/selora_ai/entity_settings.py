"""An entity's "Show as" and per-domain settings, as its settings dialog sets them.

Home Assistant's registry stores both without checking them: any string as a
device class, any mapping as options. A wrong one is not refused — it is
ignored (a sensor shown in a unit it cannot convert to keeps its own) or shown
wrongly. So what a value may be is decided here, from what Home Assistant's own
dialog offers and the tables its components read:

* **Show as** — a cover may be shown as any cover class; a binary sensor only
  as a class in the same group as its own (a door as a window, a motion sensor
  as occupancy), the groups the frontend's dialog offers.
* **Settings** — a sensor's or number's display unit, from the units its device
  class converts between; a sensor's display precision; a weather entity's
  units; a calendar's colour; a device tracker's home zone.
* **A lock's or alarm panel's default code is refused.** It is the code itself,
  stored so the lock opens without asking for it.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any, Final

from .helpers import sanitize_untrusted_text

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_registry import RegistryEntry

# The frontend's dialog (entity-registry-settings-editor, frontend 20260826):
# a cover may take any class in its list; a binary sensor only one in the same
# group as its own class.
_SHOW_AS: Final[dict[str, list[list[str]]]] = {
    "cover": [
        [
            "awning",
            "blind",
            "curtain",
            "damper",
            "door",
            "garage",
            "gate",
            "shade",
            "shutter",
            "window",
        ]
    ],
    "binary_sensor": [
        ["lock"],
        ["window", "door", "garage_door", "opening"],
        ["battery", "battery_charging"],
        ["cold", "gas", "heat"],
        ["running", "motion", "moving", "occupancy", "presence", "vibration"],
        ["power", "plug", "light"],
        [
            "smoke",
            "safety",
            "sound",
            "problem",
            "tamper",
            "carbon_monoxide",
            "moisture",
        ],
        ["connectivity"],
        ["update"],
    ],
}
_MAX_PRECISION: Final = 6
_COLOR_RE: Final = re.compile(r"#[0-9a-fA-F]{6}|[a-z][a-z_-]{0,29}")


class SettingError(ValueError):
    """A setting that would not work; the message says why."""


def show_as_choices(entry: RegistryEntry) -> list[str]:
    """The classes this entity may be shown as — none if its dialog offers none."""
    groups = _SHOW_AS.get(entry.domain, [])
    if entry.domain == "cover":
        return groups[0]
    own = entry.original_device_class
    return next((group for group in groups if own in group), [])


def check_show_as(entry: RegistryEntry, value: str) -> str:
    choices = show_as_choices(entry)
    value = str(value).strip().lower()
    if not choices:
        raise SettingError(
            f"{entry.entity_id} cannot be shown as something else. Only covers, and "
            "binary sensors within their own kind, can. To make a switch act as a "
            "light, create a switch_as_x helper."
        )
    if value not in choices:
        raise SettingError(
            f"{entry.entity_id} can be shown as {', '.join(choices)}, not "
            f"'{sanitize_untrusted_text(value, 30)}'."
        )
    return value


def _device_class(hass: HomeAssistant, entry: RegistryEntry) -> str | None:
    state = hass.states.get(entry.entity_id)
    return (
        entry.device_class
        or entry.original_device_class
        or (state.attributes.get("device_class") if state else None)
    )


def _unit(hass: HomeAssistant, entry: RegistryEntry, value: Any) -> str:
    if entry.domain == "sensor":
        from homeassistant.components.sensor.const import UNIT_CONVERTERS  # noqa: PLC0415
    else:
        from homeassistant.components.number.const import UNIT_CONVERTERS  # noqa: PLC0415

    device_class = _device_class(hass, entry)
    converter = UNIT_CONVERTERS.get(device_class)  # type: ignore[call-overload]
    if converter is None:
        raise SettingError(
            f"{entry.entity_id}'s unit cannot be changed: Home Assistant converts units "
            "only for a sensor of a measured kind (temperature, energy, speed …)."
        )
    units = sorted(str(u) for u in converter.VALID_UNITS if u is not None)
    # Home Assistant converts only FROM a unit it knows too: an entity whose own
    # unit is outside the converter keeps it, whatever is stored here.
    if entry.unit_of_measurement not in converter.VALID_UNITS:
        raise SettingError(
            f"{entry.entity_id} reports in "
            f"'{sanitize_untrusted_text(entry.unit_of_measurement or 'no unit', 20)}', which "
            "Home Assistant cannot convert, so its unit cannot be changed."
        )
    if str(value) not in units:
        raise SettingError(f"{entry.entity_id} can be shown in {', '.join(units)}.")
    return str(value)


def _precision(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int | float) or value != int(value):
        raise SettingError(
            f"display_precision is a whole number of decimals, 0 to {_MAX_PRECISION}."
        )
    if not 0 <= int(value) <= _MAX_PRECISION:
        raise SettingError(f"display_precision is 0 to {_MAX_PRECISION} decimals.")
    return int(value)


def _weather_unit(key: str, value: Any) -> str:
    from homeassistant.components.weather.const import VALID_UNITS  # noqa: PLC0415

    units = sorted(str(u) for u in VALID_UNITS[key])
    if str(value) not in units:
        raise SettingError(f"{key} is one of {', '.join(units)}.")
    return str(value)


def _zone(hass: HomeAssistant, entry: RegistryEntry, value: Any) -> str:
    # Read only by a tracker that sees the device on the network (a router,
    # Bluetooth): it marks the device home in this zone while connected. A GPS
    # tracker places itself, and older cores have no such option at all.
    if (entry.capabilities or {}).get("tracking_type") != "connection":
        raise SettingError(
            f"{entry.entity_id} places itself (GPS), or this Home Assistant has no "
            "associated zones: only a network or Bluetooth tracker takes one."
        )
    zone = str(value).strip()
    if not zone.startswith("zone.") or hass.states.get(zone) is None:
        raise SettingError(
            f"associated_zone is a zone that exists, not '{sanitize_untrusted_text(zone, 60)}'."
        )
    return zone


def _color(value: Any) -> str:
    if not _COLOR_RE.fullmatch(str(value)):
        raise SettingError("color is a colour name (e.g. 'blue') or #rrggbb.")
    return str(value)


def _keys(domain: str) -> tuple[str, ...]:
    if domain == "sensor":
        return ("unit_of_measurement", "display_precision")
    if domain == "number":
        return ("unit_of_measurement",)
    if domain == "weather":
        from homeassistant.components.weather.const import VALID_UNITS  # noqa: PLC0415

        return tuple(sorted(VALID_UNITS))
    if domain == "calendar":
        return ("color",)
    if domain == "device_tracker":
        return ("associated_zone",)
    return ()


def check_settings(
    hass: HomeAssistant, entry: RegistryEntry, settings: dict[str, Any]
) -> dict[str, Any]:
    """The entity's domain options after *settings*: each checked, ``None``
    removing one (back to the integration's default)."""
    domain = entry.domain
    if domain in ("lock", "alarm_control_panel") and "default_code" in settings:
        raise SettingError(
            "A default code is the code itself, stored so the lock or alarm works without "
            "it being asked for. It is not set from here; set it in the entity's settings."
        )
    allowed = _keys(domain)
    if not allowed:
        raise SettingError(f"A {domain} entity has no display settings to change.")
    if unknown := sorted(set(settings) - set(allowed)):
        raise SettingError(f"A {domain} entity's settings are {', '.join(allowed)}, not {unknown}.")

    merged = dict(entry.options.get(domain) or {})
    for key, value in settings.items():
        if value is None:
            merged.pop(key, None)
        elif key == "unit_of_measurement":
            merged[key] = _unit(hass, entry, value)
        elif key == "display_precision":
            merged[key] = _precision(value)
        elif key == "color":
            merged[key] = _color(value)
        elif key == "associated_zone":
            merged[key] = _zone(hass, entry, value)
        else:
            merged[key] = _weather_unit(key, value)
    return merged
