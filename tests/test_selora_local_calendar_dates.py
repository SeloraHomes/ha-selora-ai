"""All-day vs timed calendar-event date spans.

Home Assistant serialises an all-day event's ``end`` as the EXCLUSIVE next
day, so ``2026-09-09 -> 2026-09-10`` occupies the 9th alone. A timed event's
``end`` is a real instant on the last day it covers, so that one is
inclusive. Reading both the same way reported every all-day event on the day
after it finished.
"""

from __future__ import annotations

import pytest

from custom_components.selora_ai.providers.selora_local.calendar.events import (
    _CalendarMixin,
)

_on = _CalendarMixin._calendar_event_on_date


@pytest.mark.parametrize(
    ("start", "end", "day", "expected"),
    [
        # Single-day all-day event: end is the exclusive next day.
        ("2026-09-09", "2026-09-10", "2026-09-09", True),
        ("2026-09-09", "2026-09-10", "2026-09-10", False),
        ("2026-09-09", "2026-09-10", "2026-09-08", False),
        # Multi-day all-day event spanning the 9th and 10th.
        ("2026-09-09", "2026-09-11", "2026-09-09", True),
        ("2026-09-09", "2026-09-11", "2026-09-10", True),
        ("2026-09-09", "2026-09-11", "2026-09-11", False),
        # Timed event: end is an instant on the final day, so inclusive.
        ("2026-09-09T09:00:00", "2026-09-09T10:30:00", "2026-09-09", True),
        ("2026-09-09T23:00:00", "2026-09-10T01:00:00", "2026-09-10", True),
        ("2026-09-09T09:00:00", "2026-09-09T10:30:00", "2026-09-10", False),
    ],
)
def test_event_on_date(start: str, end: str, day: str, expected: bool) -> None:
    assert _on({"start": start, "end": end}, day) is expected


def test_no_today_is_in_scope() -> None:
    """A caller with no reference date wants every event."""
    assert _on({"start": "2026-09-09", "end": "2026-09-10"}, None) is True


def test_undated_event_is_in_scope() -> None:
    """An event whose start carries no parseable date is not filtered out."""
    assert _on({"start": "", "end": ""}, "2026-09-09") is True


def test_missing_end_falls_back_to_start() -> None:
    """No end means a single-day event on its start date."""
    assert _on({"start": "2026-09-09"}, "2026-09-09") is True
    assert _on({"start": "2026-09-09"}, "2026-09-10") is False
