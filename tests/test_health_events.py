"""Health issue events: debounced raised/cleared events and the entities over them."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import Mock, patch

from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_send
import pytest
from pytest_homeassistant_custom_component.common import async_capture_events

from custom_components.selora_ai.binary_sensor import HealthProblemBinarySensor
from custom_components.selora_ai.const import (
    DOMAIN,
    EVENT_HEALTH_ISSUE_CLEARED,
    EVENT_HEALTH_ISSUE_RAISED,
    SIGNAL_INSIGHTS_UPDATED,
)
from custom_components.selora_ai.health_events import HealthEventTracker, issue_id
from custom_components.selora_ai.health_store import HealthStore
from custom_components.selora_ai.insights_audit import AuditRunner
from custom_components.selora_ai.sensor import HealthIssuesSensor

from .conftest import MockStore

T0 = datetime(2026, 11, 16, 8, 0, tzinfo=UTC)


def _at(minutes: float) -> str:
    """The last_scan stamp of a health scan, minutes after T0."""
    return (T0 + timedelta(minutes=minutes)).isoformat()


@pytest.fixture
def store(hass):
    with patch("custom_components.selora_ai.health_store.Store") as mock_cls:
        mock_cls.return_value = MockStore()
        hs = HealthStore(hass)
        hs._store = MockStore()
        yield hs


def _offline(device: str = "dev1", name: str = "Freezer sensor") -> dict:
    return {
        "check_id": "offline_devices",
        "severity": "warning",
        "title": f"{name} is offline",
        "detail": f"{name} is unavailable.",
        "entities": ["sensor.freezer_temperature"],
        "device_id": device,
    }


def _battery(device: str = "dev2") -> dict:
    return {
        "check_id": "low_batteries",
        "severity": "warning",
        "title": "Replace battery: Door",
        "detail": "Door battery is low.",
        "entities": ["sensor.door_battery"],
        "device_id": device,
    }


def _results(*findings: dict, errored: tuple[str, ...] = ()) -> list:
    by_check: dict[str, list] = {}
    for f in findings:
        by_check.setdefault(f["check_id"], []).append(f)
    checks = {"offline_devices", "low_batteries", "updates_available", *errored}
    return [
        {
            "check_id": check,
            "title": check,
            "kind": "deterministic",
            "status": "error"
            if check in errored
            else ("issues" if by_check.get(check) else "clear"),
            "findings": [] if check in errored else by_check.get(check, []),
        }
        for check in sorted(checks)
    ]


async def _primed(hass, store) -> HealthEventTracker:
    """A tracker past its first (baseline) run on a healthy home."""
    tracker = HealthEventTracker(hass, store)
    await tracker.async_process(_results(), scan=_at(-60))
    return tracker


@pytest.mark.asyncio
async def test_first_run_is_a_silent_baseline(hass: HomeAssistant, store) -> None:
    """Problems standing when the tracker first runs are counted, never pushed —
    and leave without a clear, since nothing was announced."""
    raised = async_capture_events(hass, EVENT_HEALTH_ISSUE_RAISED)
    cleared = async_capture_events(hass, EVENT_HEALTH_ISSUE_CLEARED)
    tracker = HealthEventTracker(hass, store)

    await tracker.async_process(_results(_offline()), scan=_at(0))
    await tracker.async_process(_results(_offline()), scan=_at(30))
    state = await store.get_health_events()
    assert state["issues"]["offline_devices:dev1"]["state"] == "baseline"

    await tracker.async_process(_results(), scan=_at(60))
    await tracker.async_process(_results(), scan=_at(90))
    await hass.async_block_till_done()
    assert raised == [] and cleared == []
    assert (await store.get_health_events())["issues"] == {}


@pytest.mark.asyncio
async def test_raised_only_when_a_later_audit_confirms(hass: HomeAssistant, store) -> None:
    raised = async_capture_events(hass, EVENT_HEALTH_ISSUE_RAISED)
    tracker = await _primed(hass, store)

    await tracker.async_process(_results(_offline()), scan=_at(0))
    await tracker.async_process(_results(_offline()), scan=_at(5))  # inside the window
    await hass.async_block_till_done()
    assert raised == []

    await tracker.async_process(_results(_offline()), scan=_at(15))
    await tracker.async_process(_results(_offline()), scan=_at(30))  # no repeat
    await hass.async_block_till_done()
    assert len(raised) == 1
    data = raised[0].data
    assert data["issue_id"] == "offline_devices:dev1"
    assert data["check"] == "offline_devices"
    assert data["title"] == "Freezer sensor is offline"
    assert data["since"] == _at(0)
    assert data["active_issues"] == 1


@pytest.mark.asyncio
async def test_blip_between_audits_never_notifies(hass: HomeAssistant, store) -> None:
    """Found by one audit, gone by the next: no raise, no clear."""
    raised = async_capture_events(hass, EVENT_HEALTH_ISSUE_RAISED)
    cleared = async_capture_events(hass, EVENT_HEALTH_ISSUE_CLEARED)
    tracker = await _primed(hass, store)

    await tracker.async_process(_results(_offline()), scan=_at(0))
    await tracker.async_process(_results(), scan=_at(15))
    await tracker.async_process(_results(_offline()), scan=_at(30))  # starts over
    await tracker.async_process(_results(), scan=_at(45))
    await hass.async_block_till_done()
    assert raised == [] and cleared == []


@pytest.mark.asyncio
async def test_cleared_once_gone_for_the_window(hass: HomeAssistant, store) -> None:
    raised = async_capture_events(hass, EVENT_HEALTH_ISSUE_RAISED)
    cleared = async_capture_events(hass, EVENT_HEALTH_ISSUE_CLEARED)
    tracker = await _primed(hass, store)
    await tracker.async_process(_results(_offline(), _battery()), scan=_at(0))
    await tracker.async_process(_results(_offline(), _battery()), scan=_at(15))
    await hass.async_block_till_done()
    assert len(raised) == 2

    await tracker.async_process(_results(_battery()), scan=_at(30))
    await hass.async_block_till_done()
    assert cleared == []  # gone from one audit only

    await tracker.async_process(_results(_battery()), scan=_at(45))
    await hass.async_block_till_done()
    assert len(cleared) == 1
    data = cleared[0].data
    assert data["issue_id"] == "offline_devices:dev1"
    assert data["title"] == "Freezer sensor is offline"  # same wording as the raise
    assert data["active_issues"] == 1  # the battery is still open


@pytest.mark.asyncio
async def test_return_inside_the_window_cancels_the_clear(hass: HomeAssistant, store) -> None:
    cleared = async_capture_events(hass, EVENT_HEALTH_ISSUE_CLEARED)
    raised = async_capture_events(hass, EVENT_HEALTH_ISSUE_RAISED)
    tracker = await _primed(hass, store)
    await tracker.async_process(_results(_offline()), scan=_at(0))
    await tracker.async_process(_results(_offline()), scan=_at(15))

    await tracker.async_process(_results(), scan=_at(30))
    await tracker.async_process(_results(_offline()), scan=_at(35))
    await tracker.async_process(_results(), scan=_at(50))  # the missing clock restarts
    await hass.async_block_till_done()
    assert cleared == []
    assert len(raised) == 1


@pytest.mark.asyncio
async def test_errored_check_is_not_read_as_resolved(hass: HomeAssistant, store) -> None:
    cleared = async_capture_events(hass, EVENT_HEALTH_ISSUE_CLEARED)
    tracker = await _primed(hass, store)
    await tracker.async_process(_results(_offline()), scan=_at(0))
    await tracker.async_process(_results(_offline()), scan=_at(15))

    await tracker.async_process(_results(errored=("offline_devices",)), scan=_at(30))
    await tracker.async_process(_results(errored=("offline_devices",)), scan=_at(60))
    await hass.async_block_till_done()
    assert cleared == []
    issues = (await store.get_health_events())["issues"]
    assert issues["offline_devices:dev1"]["state"] == "announced"


@pytest.mark.asyncio
async def test_audit_without_a_new_scan_adds_no_evidence(hass: HomeAssistant, store) -> None:
    """The daily audit reuses the last scan's signals: running it 10+ minutes
    later must not confirm a raise (or a clear) off that one scan."""
    raised = async_capture_events(hass, EVENT_HEALTH_ISSUE_RAISED)
    tracker = await _primed(hass, store)
    await tracker.async_process(_results(_offline()), scan=_at(0))
    await tracker.async_process(_results(_offline()), scan=_at(0))
    await hass.async_block_till_done()
    assert raised == []

    # Without an explicit scan, the store's last_scan is the evidence.
    await store.set_last_scan(_at(0))
    await tracker.async_process(_results(_offline()))
    await hass.async_block_till_done()
    assert raised == []

    await store.set_last_scan(_at(15))
    await tracker.async_process(_results(_offline()))
    await hass.async_block_till_done()
    assert len(raised) == 1


@pytest.mark.asyncio
async def test_check_that_failed_first_is_baselined_when_it_succeeds(
    hass: HomeAssistant, store
) -> None:
    """A check that errored on the very first audit has never been baselined:
    its standing problems must not be announced as new once it runs."""
    raised = async_capture_events(hass, EVENT_HEALTH_ISSUE_RAISED)
    tracker = HealthEventTracker(hass, store)
    await tracker.async_process(_results(errored=("offline_devices",)), scan=_at(0))
    await tracker.async_process(_results(_offline()), scan=_at(15))
    await tracker.async_process(_results(_offline()), scan=_at(30))
    await hass.async_block_till_done()
    assert raised == []
    state = await store.get_health_events()
    assert state["issues"]["offline_devices:dev1"]["state"] == "baseline"

    # Baselined now: a new device going offline is announced.
    await tracker.async_process(_results(_offline(), _offline("dev9", "Garage")), scan=_at(45))
    await tracker.async_process(_results(_offline(), _offline("dev9", "Garage")), scan=_at(60))
    await hass.async_block_till_done()
    assert [e.data["issue_id"] for e in raised] == ["offline_devices:dev9"]


@pytest.mark.asyncio
async def test_check_that_errored_retries_on_the_same_scan(hass: HomeAssistant, store) -> None:
    """A check that errored hasn't looked at the scan yet: a later audit over
    the same scan still counts for it (and only for it)."""
    raised = async_capture_events(hass, EVENT_HEALTH_ISSUE_RAISED)
    tracker = await _primed(hass, store)
    await tracker.async_process(_results(_offline(), _battery()), scan=_at(0))
    await tracker.async_process(_results(_battery(), errored=("offline_devices",)), scan=_at(15))
    await hass.async_block_till_done()
    assert [e.data["issue_id"] for e in raised] == ["low_batteries:dev2"]

    await tracker.async_process(_results(_offline(), _battery()), scan=_at(15))
    await hass.async_block_till_done()
    assert [e.data["issue_id"] for e in raised] == [
        "low_batteries:dev2",
        "offline_devices:dev1",
    ]


@pytest.mark.asyncio
async def test_scan_taken_while_booting_is_not_evidence(hass: HomeAssistant, store) -> None:
    """The first health scan can run inside the boot-settle window; an audit
    after the deadline must not baseline or confirm off it."""
    tracker = await _primed(hass, store)
    deadline = T0 + timedelta(minutes=2)
    await tracker.async_process(_results(_offline()), scan=_at(1), not_before=deadline)
    assert (await store.get_health_events())["issues"] == {}

    await tracker.async_process(_results(_offline()), scan=_at(3), not_before=deadline)
    issues = (await store.get_health_events())["issues"]
    assert issues["offline_devices:dev1"]["since"] == _at(3)


@pytest.mark.asyncio
async def test_advice_checks_are_not_announced(hass: HomeAssistant, store) -> None:
    raised = async_capture_events(hass, EVENT_HEALTH_ISSUE_RAISED)
    tracker = await _primed(hass, store)
    update = {"check_id": "updates_available", "severity": "info", "title": "Update core"}
    await tracker.async_process(_results(update), scan=_at(0))
    await tracker.async_process(_results(update), scan=_at(15))
    await hass.async_block_till_done()
    assert raised == []


@pytest.mark.asyncio
async def test_restart_neither_repeats_nor_forgets(hass: HomeAssistant, store) -> None:
    """The records live in the store: a new tracker (a restart) doesn't raise the
    issue again, and still owes its clear."""
    raised = async_capture_events(hass, EVENT_HEALTH_ISSUE_RAISED)
    cleared = async_capture_events(hass, EVENT_HEALTH_ISSUE_CLEARED)
    tracker = await _primed(hass, store)
    await tracker.async_process(_results(_offline()), scan=_at(0))
    await tracker.async_process(_results(_offline()), scan=_at(15))

    restarted = HealthEventTracker(hass, store)
    await restarted.async_process(_results(_offline()), scan=_at(40))
    await restarted.async_process(_results(), scan=_at(55))
    await restarted.async_process(_results(), scan=_at(70))
    await hass.async_block_till_done()
    assert len(raised) == 1
    assert len(cleared) == 1


def test_issue_id_falls_back_without_a_device() -> None:
    assert issue_id(_offline()) == "offline_devices:dev1"
    no_device = {**_offline(), "device_id": None}
    assert issue_id(no_device) == "offline_devices:sensor.freezer_temperature"
    integration = {
        "check_id": "integration_errors",
        "title": "zha integration needs attention",
        "entities": [],
        "link": "/config/integrations/integration/zha",
    }
    assert issue_id(integration) == "integration_errors:/config/integrations/integration/zha"


@pytest.mark.asyncio
async def test_audit_runner_feeds_the_tracker_once_settled(
    hass: HomeAssistant, store, monkeypatch
) -> None:
    calls: list = []

    async def _checks(_hass) -> list:
        return _results(_offline())

    monkeypatch.setattr("custom_components.selora_ai.insights_checks.async_run_checks", _checks)
    runner = AuditRunner(hass, store)
    runner._events.async_process = _record_calls(calls)

    await runner.async_run()  # still settling: a boot audit is not evidence
    assert calls == []

    runner._settled = True
    await runner.async_run()
    assert len(calls) == 1


def _record_calls(calls: list):
    async def _process(results, scan=None, *, not_before=None) -> None:
        calls.append(results)

    return _process


@pytest.mark.asyncio
async def test_entities_mirror_confirmed_issues(hass: HomeAssistant, store) -> None:
    sensor = HealthIssuesSensor(hass)
    problem = HealthProblemBinarySensor(hass)
    for entity in (sensor, problem):
        entity.async_write_ha_state = Mock()
    hass.data.setdefault(DOMAIN, {})["e1"] = {"health_store": store}

    # Insights wired but no audit yet: unavailable, not "no problem".
    await sensor.async_added_to_hass()
    await problem.async_added_to_hass()
    assert sensor.available is False and problem.available is False

    tracker = await _primed(hass, store)
    await tracker.async_process(_results(_offline(), _battery()), scan=_at(0))
    async_dispatcher_send(hass, SIGNAL_INSIGHTS_UPDATED)
    await hass.async_block_till_done()
    assert sensor.available is True
    assert sensor.native_value == 0  # pending issues aren't reported yet
    assert problem.is_on is False

    await tracker.async_process(_results(_offline(), _battery()), scan=_at(15))
    async_dispatcher_send(hass, SIGNAL_INSIGHTS_UPDATED)
    await hass.async_block_till_done()
    assert sensor.native_value == 2
    assert problem.is_on is True
    attrs = sensor.extra_state_attributes
    assert attrs["offline_devices"] == 1
    assert attrs["low_batteries"] == 1
    assert attrs["unstable_devices"] == 0
    assert {i["issue_id"] for i in attrs["issues"]} == {
        "offline_devices:dev1",
        "low_batteries:dev2",
    }


@pytest.mark.asyncio
async def test_entities_unavailable_without_insights(hass: HomeAssistant) -> None:
    hass.data.setdefault(DOMAIN, {})
    sensor = HealthIssuesSensor(hass)
    await sensor._async_refresh()
    assert sensor.available is False
