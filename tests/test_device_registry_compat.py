"""`helpers.device_entries` over both shapes of `DeviceRegistry.devices`."""

from __future__ import annotations

from unittest.mock import MagicMock

from custom_components.selora_ai.helpers import device_entries

from .registry_view import DeviceRegistryView


def _device(device_id: str) -> MagicMock:
    device = MagicMock()
    device.id = device_id
    return device


def test_reads_the_mapping_home_assistant_served_until_2026_9() -> None:
    registry = MagicMock()
    devices = [_device("dev_a"), _device("dev_b")]
    registry.devices = {d.id: d for d in devices}
    assert device_entries(registry) == devices


def test_reads_the_view_home_assistant_serves_from_2026_9() -> None:
    """Iterating the view yields the entries — and `values()` raises here, so
    this also proves the mapping branch is not the one that ran."""
    registry = MagicMock()
    devices = [_device("dev_a"), _device("dev_b")]
    registry.devices = DeviceRegistryView(devices)
    assert device_entries(registry) == devices


def test_an_empty_registry_is_empty_either_way() -> None:
    mapping, view = MagicMock(), MagicMock()
    mapping.devices = {}
    view.devices = DeviceRegistryView([])
    assert device_entries(mapping) == []
    assert device_entries(view) == []


def test_the_shape_decides_rather_than_a_version() -> None:
    """The mapping branch is chosen by `isinstance(..., Mapping)` — never by
    asking the object what it supports. The 2026.9 view answers an unknown
    attribute through `__getattr__`, which is itself one of the deprecated
    accesses, so `hasattr(devices, "values")` would report the deprecation it
    is trying to avoid."""
    registry = MagicMock()
    registry.devices = DeviceRegistryView([_device("dev_a")])
    assert [d.id for d in device_entries(registry)] == ["dev_a"]
