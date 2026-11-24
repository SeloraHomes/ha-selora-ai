"""The support-session outbox Selora OS forwards to Connect."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
from pathlib import Path

from homeassistant.core import HomeAssistant
import pytest

from custom_components.selora_ai import support_activity
from custom_components.selora_ai.support_activity import async_record, outbox_path


def _events(hass: HomeAssistant) -> list[dict]:
    outbox = outbox_path(hass)
    return [json.loads(p.read_text()) for p in sorted(outbox.glob("*.json"))]


async def test_an_event_is_one_immutable_file_named_in_time_order(hass: HomeAssistant) -> None:
    await async_record(hass, 42, "access_requested", {"folder": "www/selora"})
    await async_record(hass, 42, "file_written", {"file": "www/selora/a.js"})

    first, second = _events(hass)
    assert first["type"] == "access_requested"
    assert second["type"] == "file_written"
    assert first["session_id"] == 42
    assert first["schema_version"] == 1
    assert first["event_id"] != second["event_id"]
    # Owner-only, and nothing half-written for the host to pick up.
    outbox = outbox_path(hass)
    assert all(p.stat().st_mode & 0o777 == 0o600 for p in outbox.glob("*.json"))
    assert not list(outbox.glob(".*"))


async def test_the_outbox_prunes_by_age_and_count(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    outbox = outbox_path(hass)
    outbox.mkdir(parents=True)
    old = (datetime.now(UTC) - timedelta(days=31)).strftime("%Y%m%dT%H%M%S%fZ")
    Path(outbox, f"{old}-x.json").write_text("{}")
    monkeypatch.setattr(support_activity, "_MAX_FILES", 2)

    for n in range(3):
        await async_record(hass, 1, "file_written", {"n": n})

    assert [e["n"] for e in _events(hass)] == [1, 2]


async def test_a_full_disk_does_not_fail_the_change(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _fail(*_args: object) -> None:
        raise OSError("No space left on device")

    monkeypatch.setattr(support_activity, "_write", _fail)
    await async_record(hass, 1, "file_written", {})
