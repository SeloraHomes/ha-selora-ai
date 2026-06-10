"""Tests for EntityFilter dropping diagnostic + config entities.

A typical Zigbee/Z-Wave/Matter device ships 3-5 controllable entities
(switches, lights, climate setpoints) plus 10-20 diagnostic ones
(battery level, RSSI / LQI, firmware version, identify button, last
seen, etc). Sending the diagnostic ones to the LLM is pure noise —
they crowd the entity context and don't help the model answer.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from custom_components.selora_ai.entity_filter import EntityFilter


def _entry(disabled: bool = False, entity_category: object = None, device_id: str | None = None):
    e = MagicMock()
    e.disabled = disabled
    e.entity_category = entity_category
    e.device_id = device_id
    return e


def _filter(hass, entries: dict) -> EntityFilter:
    """Build an EntityFilter against a registry stub that returns each
    entity's mock entry."""
    reg = MagicMock()
    reg.async_get.side_effect = lambda eid: entries.get(eid)
    with patch("homeassistant.helpers.entity_registry.async_get", return_value=reg):
        return EntityFilter(hass, list(entries.keys()))


def test_normal_entity_passes(hass) -> None:
    """No disabled flag + no entity_category → active."""
    ef = _filter(hass, {"light.kitchen": _entry()})
    assert ef.is_active("light.kitchen") is True


def test_disabled_entity_dropped(hass) -> None:
    """disabled=True (registry-disabled, or integration-disabled) → inactive."""
    ef = _filter(hass, {"switch.unused": _entry(disabled=True)})
    assert ef.is_active("switch.unused") is False


def test_diagnostic_entity_dropped(hass) -> None:
    """entity_category="diagnostic" → inactive (battery levels, RSSI, etc).

    The exact value the registry stores is the EntityCategory.DIAGNOSTIC
    enum, but the filter only checks ``is not None`` — any non-None
    category means the entity is config or diagnostic, not user-facing."""
    ef = _filter(hass, {"sensor.battery_level": _entry(entity_category="diagnostic")})
    assert ef.is_active("sensor.battery_level") is False


def test_config_entity_dropped(hass) -> None:
    """entity_category="config" → inactive (identify button, polling rate)."""
    ef = _filter(hass, {"button.identify": _entry(entity_category="config")})
    assert ef.is_active("button.identify") is False


def test_zooz_relay_like_device(hass) -> None:
    """Realistic 3-controllable + 17-diagnostic device profile (a 3-in-1
    relay): only the controllable switches should survive."""
    entries = {
        # 3 controllable switches (what the user actually automates)
        "switch.zooz_relay_high_speed": _entry(device_id="dev_zooz"),
        "switch.zooz_relay_low_speed": _entry(device_id="dev_zooz"),
        "switch.zooz_relay_low_power": _entry(device_id="dev_zooz"),
        # 17 diagnostics (battery, signal, firmware, identify, ...)
        "sensor.zooz_battery_level": _entry(entity_category="diagnostic", device_id="dev_zooz"),
        "sensor.zooz_signal_strength": _entry(entity_category="diagnostic", device_id="dev_zooz"),
        "sensor.zooz_firmware": _entry(entity_category="diagnostic", device_id="dev_zooz"),
        "sensor.zooz_last_seen": _entry(entity_category="diagnostic", device_id="dev_zooz"),
        "sensor.zooz_rssi": _entry(entity_category="diagnostic", device_id="dev_zooz"),
        "sensor.zooz_lqi": _entry(entity_category="diagnostic", device_id="dev_zooz"),
        "sensor.zooz_power_w": _entry(entity_category="diagnostic", device_id="dev_zooz"),
        "sensor.zooz_energy_kwh": _entry(entity_category="diagnostic", device_id="dev_zooz"),
        "sensor.zooz_uptime": _entry(entity_category="diagnostic", device_id="dev_zooz"),
        "sensor.zooz_temperature": _entry(entity_category="diagnostic", device_id="dev_zooz"),
        "button.zooz_identify": _entry(entity_category="config", device_id="dev_zooz"),
        "button.zooz_reset": _entry(entity_category="config", device_id="dev_zooz"),
        "number.zooz_polling": _entry(entity_category="config", device_id="dev_zooz"),
        "number.zooz_threshold": _entry(entity_category="config", device_id="dev_zooz"),
        "select.zooz_mode": _entry(entity_category="config", device_id="dev_zooz"),
        "switch.zooz_led": _entry(entity_category="config", device_id="dev_zooz"),
        "switch.zooz_logging": _entry(entity_category="config", device_id="dev_zooz"),
    }
    ef = _filter(hass, entries)
    surviving = [eid for eid in entries if ef.is_active(eid)]
    assert sorted(surviving) == [
        "switch.zooz_relay_high_speed",
        "switch.zooz_relay_low_power",
        "switch.zooz_relay_low_speed",
    ]


def test_unknown_entity_is_active(hass) -> None:
    """Entity not in the registry (or never queried at construction) is
    treated as active — defensive against a missing registry entry, the
    pattern engines / collector then exclude it through other gates."""
    ef = _filter(hass, {})
    assert ef.is_active("light.never_registered") is True


def test_same_device_query_still_works(hass) -> None:
    """The is_active extension must not regress the same_device helper."""
    entries = {
        "switch.a": _entry(device_id="dev_x"),
        "sensor.b": _entry(entity_category="diagnostic", device_id="dev_x"),
        "switch.c": _entry(device_id="dev_y"),
    }
    ef = _filter(hass, entries)
    assert ef.same_device("switch.a", "sensor.b") is True
    assert ef.same_device("switch.a", "switch.c") is False
