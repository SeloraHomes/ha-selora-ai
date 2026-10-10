"""Reading a device's config entries on either side of HA's single-entry change."""

from __future__ import annotations

from types import SimpleNamespace

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.selora_ai.helpers import (
    device_config_entry_ids,
    device_primary_config_entry,
)


class _NewCoreDevice:
    """A 2026.9+ entry whose deprecated shims fail the test when read.

    On the core that removes them the shims log a deprecation for every read,
    which is what this guards: the helpers must never touch them there.
    """

    def __init__(self, config_entry_id: str) -> None:
        self.config_entry_id = config_entry_id

    @property
    def config_entries(self) -> set[str]:
        raise AssertionError("read the deprecated config_entries shim")

    @property
    def primary_config_entry(self) -> str:
        raise AssertionError("read the deprecated primary_config_entry shim")


def test_new_core_reads_config_entry_id_only() -> None:
    device = _NewCoreDevice("entry1")
    assert device_config_entry_ids(device) == ("entry1",)  # type: ignore[arg-type]
    assert device_primary_config_entry(device) == "entry1"  # type: ignore[arg-type]


def test_new_core_composite_reports_every_owner() -> None:
    # async_get synthesizes this for a pre-migration id: config_entry_id is the
    # former primary only, the union of the split devices' owners lives aside.
    device = _NewCoreDevice("primary")
    device._composite_subentries = {"primary": {None}, "other": {None}}  # type: ignore[attr-defined]
    assert sorted(device_config_entry_ids(device)) == ["other", "primary"]  # type: ignore[arg-type]
    assert device_primary_config_entry(device) == "primary"  # type: ignore[arg-type]


def test_old_core_reads_the_set() -> None:
    device = SimpleNamespace(config_entries={"a", "b"}, primary_config_entry="b")
    assert sorted(device_config_entry_ids(device)) == ["a", "b"]  # type: ignore[arg-type]
    assert device_primary_config_entry(device) == "b"  # type: ignore[arg-type]


def test_old_core_without_a_primary_falls_back_to_any_entry() -> None:
    device = SimpleNamespace(config_entries={"only"}, primary_config_entry=None)
    assert device_primary_config_entry(device) == "only"  # type: ignore[arg-type]


def test_old_core_device_with_no_entries() -> None:
    device = SimpleNamespace(config_entries=set(), primary_config_entry=None)
    assert device_config_entry_ids(device) == ()  # type: ignore[arg-type]
    assert device_primary_config_entry(device) is None  # type: ignore[arg-type]


async def test_a_real_registry_entry(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(domain="demo")
    entry.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={("demo", "d1")}
    )
    assert device_config_entry_ids(device) == (entry.entry_id,)
    assert device_primary_config_entry(device) == entry.entry_id
