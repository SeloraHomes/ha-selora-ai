"""The two shapes `DeviceRegistry.devices` takes, for tests that cross them.

Home Assistant 2026.9 replaced the device registry's `device_id -> entry`
mapping with a view that yields the ENTRIES when iterated and reports every
mapping access as deprecated. Iterating the old mapping yields device IDS, so
code written for one shape reads silently wrong against the other — the reason
`helpers.device_entries` exists, and the reason a test that only ever sees the
mapping proves nothing about the core our users are moving to.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any


class DeviceRegistryView:
    """`registry.devices` as HA serves it from 2026.9.

    `values()` raises rather than returning: the real view still answers it and
    files a deprecation report, which the test harness escalates to an error,
    so a call here would be a CI failure the day the pin lifts.
    """

    def __init__(self, entries: list[Any]) -> None:
        self._entries = list(entries)

    def __iter__(self) -> Iterator[Any]:
        return iter(self._entries)

    def __len__(self) -> int:
        return len(self._entries)

    def __contains__(self, obj: object) -> bool:
        return obj in self._entries

    def values(self) -> Any:
        raise AssertionError("mapping access on the 2026.9 device registry view")

    def get(self, key: str, default: Any = None) -> Any:
        raise AssertionError("mapping access on the 2026.9 device registry view")
