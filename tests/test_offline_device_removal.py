"""Deleting a device that has been offline long enough to be gone for good.

Covers the three pieces that have to agree: the offline check deciding whether
to offer the action (``removable``), the removal itself (HA's release-then-detach
path), and the websocket handler re-verifying both before it deletes anything.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    MockModule,
    mock_integration,
)

from custom_components.selora_ai.const import DOMAIN, HEALTH_OFFLINE_REMOVABLE_SECS
from custom_components.selora_ai.device_removal import (
    DeviceRespondingError,
    async_remove_device,
    device_is_removable,
)
from custom_components.selora_ai.health_store import HealthStore
from custom_components.selora_ai.insights_checks import (
    async_run_checks,
    device_offline_seconds,
    signal_offline_seconds,
)
from custom_components.selora_ai.websocket.insights import _handle_delete_device

from .conftest import MockStore

_delete_device_handler = _handle_delete_device.__wrapped__

_WEEK = HEALTH_OFFLINE_REMOVABLE_SECS


@pytest.fixture
def health_store(hass: HomeAssistant) -> Iterator[HealthStore]:
    with patch("custom_components.selora_ai.health_store.Store") as mock_cls:
        mock_cls.return_value = MockStore()
        hs = HealthStore(hass)
        hs._store = MockStore()
        yield hs


@pytest.fixture
def mock_connection() -> MagicMock:
    conn = MagicMock()
    conn.user.is_admin = True
    return conn


def _speaker(
    hass: HomeAssistant, *, supports_remove: bool, domain: str = "sonos"
) -> tuple[dr.DeviceEntry, str]:
    """A one-entity device owned by ``domain``, whose integration does (or does
    not) implement the device-removal hook."""
    entry = MockConfigEntry(domain=domain, title=domain.title())
    entry.add_to_hass(hass)
    entry.supports_remove_device = supports_remove
    mock_integration(
        hass,
        MockModule(
            domain,
            async_remove_config_entry_device=AsyncMock(return_value=True)
            if supports_remove
            else None,
        ),
    )
    dev_reg = dr.async_get(hass)
    ent_reg = er.async_get(hass)
    device = dev_reg.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(domain, "bedroom")},
        name="Bedroom",
    )
    ent = ent_reg.async_get_or_create(
        "media_player", domain, "bedroom", device_id=device.id, config_entry=entry
    )
    return device, ent.entity_id


async def _record_offline(store: HealthStore, entity_id: str, device_id: str, secs: int) -> None:
    await store.record_signal(
        kind="unavailable",
        target=entity_id,
        target_kind="entity",
        severity="warning",
        evidence={"state": "unavailable", "unavailable_seconds": secs},
        device_id=device_id,
    )


# ── Signal age helpers ────────────────────────────────────────────────


def test_offline_seconds_prefers_monitor_evidence() -> None:
    """The monitor's own duration wins — it survives restarts, first_seen only
    dates the signal record."""
    sig = {
        "kind": "unavailable",
        "evidence": {"unavailable_seconds": 900_000},
        "first_seen": datetime.now(UTC).isoformat(),
    }
    assert signal_offline_seconds(sig) == 900_000


def test_offline_seconds_falls_back_to_first_seen() -> None:
    """A signal recorded without the evidence key still ages, so a pre-existing
    record can't read as "just went offline" forever."""
    sig = {
        "kind": "unavailable",
        "evidence": {},
        "first_seen": (datetime.now(UTC) - timedelta(days=9)).isoformat(),
    }
    assert signal_offline_seconds(sig) >= 9 * 86400 - 60


def test_offline_seconds_survives_a_partial_record() -> None:
    """No usable duration reads as 0 ("not long enough"), never a crash."""
    assert signal_offline_seconds({"kind": "unavailable"}) == 0
    assert signal_offline_seconds({"kind": "unavailable", "first_seen": "not-a-date"}) == 0


def test_device_offline_seconds_takes_the_newest_outage() -> None:
    """The device went dark when its last answering entity dropped, so the newest
    outage bounds it. An entity that was already unavailable for a week while the
    device worked — the monitor re-raises that signal anchored weeks back once the
    device does go down — must not date the device's outage."""
    signals: list[dict[str, Any]] = [
        {"kind": "unavailable", "device_id": "d1", "evidence": {"unavailable_seconds": 120}},
        {"kind": "unavailable", "device_id": "d1", "evidence": {"unavailable_seconds": _WEEK}},
        {"kind": "battery_low", "device_id": "d1", "evidence": {"unavailable_seconds": 999_999}},
        {"kind": "unavailable", "device_id": "d2", "evidence": {"unavailable_seconds": 999_999}},
    ]
    assert device_offline_seconds(signals, "d1") == 120
    assert device_offline_seconds(signals, "d2") == 999_999
    assert device_offline_seconds(signals, "unknown") == 0


# ── device_is_removable ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_removable_when_the_integration_supports_it(hass: HomeAssistant) -> None:
    device, _ = _speaker(hass, supports_remove=True)
    assert device_is_removable(hass, device.id) is True


@pytest.mark.asyncio
async def test_not_removable_when_the_integration_does_not(hass: HomeAssistant) -> None:
    """HA hides its own delete button for these; we must not offer one either."""
    device, _ = _speaker(hass, supports_remove=False)
    assert device_is_removable(hass, device.id) is False


def _multi_owner_device(hass: HomeAssistant, *supports: bool) -> Any:
    """Patch in a device reporting several owning config entries.

    Current HA gives a device exactly one config entry — ``config_entries`` is a
    compatibility shim that reports more than one only for a pre-migration
    composite device, which the registry synthesizes and no public API can
    build. So the shape the shim still yields is stubbed here rather than
    constructed, and the patch is what makes the helpers read it.
    """
    entry_ids = []
    for index, supported in enumerate(supports):
        entry = MockConfigEntry(domain=f"owner{index}", title=f"Owner {index}")
        entry.add_to_hass(hass)
        entry.supports_remove_device = supported
        entry_ids.append(entry.entry_id)
    device = SimpleNamespace(
        id="shared", name="Shared", name_by_user=None, config_entries=set(entry_ids)
    )
    registry = MagicMock()
    registry.async_get.return_value = device
    return patch("custom_components.selora_ai.device_removal.dr.async_get", return_value=registry)


def test_not_removable_when_one_of_two_owners_refuses(hass: HomeAssistant) -> None:
    """A device shared by two integrations survives the delete when only one can
    release it — offering the action there would report a success the user can
    still see on their device page."""
    with _multi_owner_device(hass, True, False):
        assert device_is_removable(hass, "shared") is False


def test_not_removable_when_shared_even_if_every_owner_supports_it(
    hass: HomeAssistant,
) -> None:
    """Still refused: the hook IS the removal, so there is no way to learn a
    later owner would refuse without the earlier one having already released.
    Support from every owner does not make the sequence atomic."""
    with _multi_owner_device(hass, True, True):
        assert device_is_removable(hass, "shared") is False


@pytest.mark.asyncio
async def test_remove_device_refuses_a_shared_device_before_touching_it(
    hass: HomeAssistant,
) -> None:
    """The removal enforces the same rule, so nothing is released on the way to
    discovering the device can't be deleted whole."""
    with _multi_owner_device(hass, True, True) as async_get:
        with pytest.raises(HomeAssistantError):
            await async_remove_device(hass, "shared")
        registry = async_get.return_value
        registry.async_remove_device.assert_not_called()
        registry.async_update_device.assert_not_called()


def test_unknown_device_is_not_removable(hass: HomeAssistant) -> None:
    assert device_is_removable(hass, "nope") is False


# ── async_remove_device ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_remove_device_drops_device_and_entities(hass: HomeAssistant) -> None:
    """Release + detach deletes the device, and HA's registry follows with its
    entities."""
    device, entity_id = _speaker(hass, supports_remove=True)

    name = await async_remove_device(hass, device.id)

    assert name == "Bedroom"
    assert dr.async_get(hass).async_get(device.id) is None
    await hass.async_block_till_done()
    assert er.async_get(hass).async_get(entity_id) is None


@pytest.mark.asyncio
async def test_remove_device_raises_when_the_integration_refuses(hass: HomeAssistant) -> None:
    """A hook returning False means "not mine to delete" — surface that instead
    of reporting a deletion that didn't happen."""
    entry = MockConfigEntry(domain="refuser", title="Refuser")
    entry.add_to_hass(hass)
    entry.supports_remove_device = True
    mock_integration(
        hass,
        MockModule("refuser", async_remove_config_entry_device=AsyncMock(return_value=False)),
    )
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={("refuser", "x")}, name="Thing"
    )

    with pytest.raises(HomeAssistantError):
        await async_remove_device(hass, device.id)
    assert dr.async_get(hass).async_get(device.id) is not None


@pytest.mark.asyncio
async def test_remove_device_aborts_when_it_wakes_before_the_hook(hass: HomeAssistant) -> None:
    """Loading the owning integration awaits, so the caller's liveness check is
    already stale when the destructive hook is about to fire. ``still_gone`` is
    re-read there, and a device answering by then is left alone."""
    hook = AsyncMock(return_value=True)
    entry = MockConfigEntry(domain="waker", title="Waker")
    entry.add_to_hass(hass)
    entry.supports_remove_device = True
    mock_integration(hass, MockModule("waker", async_remove_config_entry_device=hook))
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={("waker", "x")}, name="Thing"
    )
    calls: list[bool] = []

    def _still_gone() -> bool:
        calls.append(True)  # answered again while the integration was loading
        return False

    with pytest.raises(DeviceRespondingError):
        await async_remove_device(hass, device.id, still_gone=_still_gone)

    assert calls, "the check must run after the load, not only before the call"
    hook.assert_not_awaited()
    assert dr.async_get(hass).async_get(device.id) is not None


@pytest.mark.asyncio
async def test_remove_unknown_device_raises(hass: HomeAssistant) -> None:
    with pytest.raises(HomeAssistantError):
        await async_remove_device(hass, "nope")


# ── The offline check's delete offer ──────────────────────────────────


async def _offline_finding(hass: HomeAssistant) -> dict[str, Any]:
    results = {r["check_id"]: r for r in await async_run_checks(hass)}
    findings = results["offline_devices"]["findings"]
    assert len(findings) == 1
    return findings[0]


@pytest.mark.asyncio
async def test_long_offline_device_offers_deletion(
    hass: HomeAssistant, health_store: HealthStore
) -> None:
    """Past the threshold, the card carries the duration, the bare device name,
    and the delete offer."""
    hass.data.setdefault(DOMAIN, {})["e1"] = {"health_store": health_store}
    device, entity_id = _speaker(hass, supports_remove=True)
    await _record_offline(health_store, entity_id, device.id, 9 * 86400)

    finding = await _offline_finding(hass)
    assert finding["removable"] is True
    assert finding["device_name"] == "Bedroom"
    assert finding["offline_seconds"] == 9 * 86400
    assert "offline for 9 days" in finding["detail"]


@pytest.mark.asyncio
async def test_briefly_offline_device_offers_no_deletion(
    hass: HomeAssistant, health_store: HealthStore
) -> None:
    """An hour down is an outage to investigate, not a device to delete."""
    hass.data.setdefault(DOMAIN, {})["e1"] = {"health_store": health_store}
    device, entity_id = _speaker(hass, supports_remove=True)
    await _record_offline(health_store, entity_id, device.id, 3600)

    finding = await _offline_finding(hass)
    assert "removable" not in finding
    assert "device_name" not in finding
    assert finding["offline_seconds"] == 3600
    assert "offline for" not in finding["detail"]


@pytest.mark.asyncio
async def test_no_deletion_offer_when_the_integration_cannot_delete(
    hass: HomeAssistant, health_store: HealthStore
) -> None:
    """Long gone, but the owning integration can't release it — the card stays
    informational rather than showing a button that would fail."""
    hass.data.setdefault(DOMAIN, {})["e1"] = {"health_store": health_store}
    device, entity_id = _speaker(hass, supports_remove=False)
    await _record_offline(health_store, entity_id, device.id, 30 * 86400)

    finding = await _offline_finding(hass)
    assert "removable" not in finding
    assert finding["offline_seconds"] == 30 * 86400


async def _stale_entity_plus_fresh_outage(
    hass: HomeAssistant, store: HealthStore
) -> tuple[dr.DeviceEntry, str]:
    """A device that only just went dark, carrying one entity whose own outage is
    a month old: it sat unavailable while the device worked (the monitor holds
    those signals back until the whole device is down, then re-raises them
    anchored to that entity's last_changed, a month back)."""
    device, media = _speaker(hass, supports_remove=True)
    charging = er.async_get(hass).async_get_or_create(
        "sensor", "sonos", "bedroom_charging", device_id=device.id
    )
    hass.states.async_set(media, "unavailable")
    hass.states.async_set(charging.entity_id, "unavailable")
    await _record_offline(store, charging.entity_id, device.id, 30 * 86400)
    await _record_offline(store, media, device.id, 600)
    return device, media


@pytest.mark.asyncio
async def test_a_just_dark_device_is_not_offered_for_deletion(
    hass: HomeAssistant, health_store: HealthStore
) -> None:
    """The card dates the outage from when the device went dark, not from the
    oldest entity — otherwise it offers to delete a device 10 minutes into a
    fault and states a month-long outage that never happened."""
    hass.data.setdefault(DOMAIN, {})["e1"] = {"health_store": health_store}
    await _stale_entity_plus_fresh_outage(hass, health_store)

    finding = await _offline_finding(hass)
    assert finding["offline_seconds"] == 600
    assert "removable" not in finding
    assert "offline for" not in finding["detail"]


# ── Websocket handler ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_delete_device_command_removes_it(
    hass: HomeAssistant, health_store: HealthStore, mock_connection: MagicMock
) -> None:
    hass.data.setdefault(DOMAIN, {})["e1"] = {
        "health_store": health_store,
        "insights_engine": MagicMock(),
    }
    device, entity_id = _speaker(hass, supports_remove=True)
    hass.states.async_set(entity_id, "unavailable")
    await _record_offline(health_store, entity_id, device.id, 9 * 86400)

    msg = {"id": 1, "type": "selora_ai/insights/delete_device", "device_id": device.id}
    await _delete_device_handler(hass, mock_connection, msg)

    mock_connection.send_result.assert_called_once_with(1, {"success": True, "name": "Bedroom"})
    assert dr.async_get(hass).async_get(device.id) is None


@pytest.mark.asyncio
async def test_delete_device_command_removes_a_device_with_no_states(
    hass: HomeAssistant, health_store: HealthStore, mock_connection: MagicMock
) -> None:
    """The primary case: an integration drops an offline device's entities from
    the state machine on its rediscovery cycle. No state is absence of life, not
    proof of it — the live gate must not make these devices undeletable."""
    hass.data.setdefault(DOMAIN, {})["e1"] = {
        "health_store": health_store,
        "insights_engine": MagicMock(),
    }
    device, entity_id = _speaker(hass, supports_remove=True)
    assert hass.states.get(entity_id) is None
    await _record_offline(health_store, entity_id, device.id, 9 * 86400)

    msg = {"id": 1, "type": "selora_ai/insights/delete_device", "device_id": device.id}
    await _delete_device_handler(hass, mock_connection, msg)

    mock_connection.send_result.assert_called_once()
    assert dr.async_get(hass).async_get(device.id) is None


@pytest.mark.asyncio
async def test_delete_device_command_refuses_a_device_reporting_again(
    hass: HomeAssistant, health_store: HealthStore, mock_connection: MagicMock
) -> None:
    """Signals are only as fresh as the last scan, so a device that came back
    still carries a week-old outage signal. Live state is what authorizes the
    delete — the cached duration alone must not."""
    hass.data.setdefault(DOMAIN, {})["e1"] = {
        "health_store": health_store,
        "insights_engine": MagicMock(),
    }
    device, entity_id = _speaker(hass, supports_remove=True)
    await _record_offline(health_store, entity_id, device.id, 9 * 86400)
    hass.states.async_set(entity_id, "playing")

    msg = {"id": 1, "type": "selora_ai/insights/delete_device", "device_id": device.id}
    await _delete_device_handler(hass, mock_connection, msg)

    assert mock_connection.send_error.call_args[0][1] == "device_responding"
    assert dr.async_get(hass).async_get(device.id) is not None


@pytest.mark.asyncio
async def test_delete_device_command_refuses_a_partially_recovered_device(
    hass: HomeAssistant, health_store: HealthStore, mock_connection: MagicMock
) -> None:
    """One long-unavailable entity does not make the device gone: while the scan
    that would drop its stale signal hasn't run, a device with a live *primary*
    entity must still be refused."""
    hass.data.setdefault(DOMAIN, {})["e1"] = {
        "health_store": health_store,
        "insights_engine": MagicMock(),
    }
    device, entity_id = _speaker(hass, supports_remove=True)
    # No entity_category: a second functional entity, not an auxiliary one, so
    # its reading is real evidence the device answers.
    second = er.async_get(hass).async_get_or_create(
        "sensor", "sonos", "bedroom_wifi", device_id=device.id
    )
    hass.states.async_set(entity_id, "unavailable")
    hass.states.async_set(second.entity_id, "-52")
    await _record_offline(health_store, entity_id, device.id, 30 * 86400)

    msg = {"id": 1, "type": "selora_ai/insights/delete_device", "device_id": device.id}
    await _delete_device_handler(hass, mock_connection, msg)

    assert mock_connection.send_error.call_args[0][1] == "device_responding"
    assert dr.async_get(hass).async_get(device.id) is not None


@pytest.mark.asyncio
async def test_delete_device_command_ignores_cached_auxiliary_entities(
    hass: HomeAssistant, health_store: HealthStore, mock_connection: MagicMock
) -> None:
    """The archetypal offline speaker: HA holds its config entities at their last
    cached value while the media_player is unavailable. Counting those as signs
    of life would mark the card removable and then refuse every click — the gate
    judges by primary entities, the same rule that raised the signal."""
    hass.data.setdefault(DOMAIN, {})["e1"] = {
        "health_store": health_store,
        "insights_engine": MagicMock(),
    }
    device, entity_id = _speaker(hass, supports_remove=True)
    bass = er.async_get(hass).async_get_or_create(
        "number",
        "sonos",
        "bedroom_bass",
        device_id=device.id,
        entity_category=EntityCategory.CONFIG,
    )
    hass.states.async_set(entity_id, "unavailable")
    hass.states.async_set(bass.entity_id, "6")  # cached, still reads normally
    await _record_offline(health_store, entity_id, device.id, 9 * 86400)

    msg = {"id": 1, "type": "selora_ai/insights/delete_device", "device_id": device.id}
    await _delete_device_handler(hass, mock_connection, msg)

    mock_connection.send_result.assert_called_once()
    assert dr.async_get(hass).async_get(device.id) is None


@pytest.mark.asyncio
async def test_delete_device_command_deletes_on_an_unknown_state(
    hass: HomeAssistant, health_store: HealthStore, mock_connection: MagicMock
) -> None:
    """``unknown`` doesn't prove a device responds — the monitor counts it as
    unreachable, so it raised the signal. The gate has to read it the same way or
    the card offers a delete that refuses."""
    hass.data.setdefault(DOMAIN, {})["e1"] = {
        "health_store": health_store,
        "insights_engine": MagicMock(),
    }
    device, entity_id = _speaker(hass, supports_remove=True)
    hass.states.async_set(entity_id, "unknown")
    await _record_offline(health_store, entity_id, device.id, 9 * 86400)

    msg = {"id": 1, "type": "selora_ai/insights/delete_device", "device_id": device.id}
    await _delete_device_handler(hass, mock_connection, msg)

    mock_connection.send_result.assert_called_once()
    assert dr.async_get(hass).async_get(device.id) is None


@pytest.mark.asyncio
async def test_delete_device_command_refuses_a_recovered_device(
    hass: HomeAssistant, health_store: HealthStore, mock_connection: MagicMock
) -> None:
    """The card the click came from may be minutes old. A device that came back
    (its signal resolved) must not be deleted on a stale button."""
    hass.data.setdefault(DOMAIN, {})["e1"] = {
        "health_store": health_store,
        "insights_engine": MagicMock(),
    }
    device, entity_id = _speaker(hass, supports_remove=True)
    await _record_offline(health_store, entity_id, device.id, 9 * 86400)
    await health_store.resolve_signal("unavailable", entity_id)

    msg = {"id": 1, "type": "selora_ai/insights/delete_device", "device_id": device.id}
    await _delete_device_handler(hass, mock_connection, msg)

    assert mock_connection.send_error.call_args[0][1] == "not_offline"
    assert dr.async_get(hass).async_get(device.id) is not None


@pytest.mark.asyncio
async def test_delete_device_command_rescans_before_reading_the_duration(
    hass: HomeAssistant, health_store: HealthStore, mock_connection: MagicMock
) -> None:
    """The store is a cache up to a scan interval old, so the handler reconciles
    first: a scan that resolves the outage leaves nothing to authorize a delete."""
    device, entity_id = _speaker(hass, supports_remove=True)
    await _record_offline(health_store, entity_id, device.id, 9 * 86400)

    async def _scan(trigger_audit: bool = True) -> None:
        await health_store.resolve_signal("unavailable", entity_id)

    monitor = MagicMock()
    monitor.async_request_scan = AsyncMock(side_effect=_scan)
    hass.data.setdefault(DOMAIN, {})["e1"] = {
        "health_store": health_store,
        "insights_engine": MagicMock(),
        "health_monitor": monitor,
    }

    msg = {"id": 1, "type": "selora_ai/insights/delete_device", "device_id": device.id}
    await _delete_device_handler(hass, mock_connection, msg)

    assert monitor.async_request_scan.await_count == 1
    assert mock_connection.send_error.call_args[0][1] == "not_offline"
    assert dr.async_get(hass).async_get(device.id) is not None


@pytest.mark.asyncio
async def test_delete_device_command_refuses_a_device_that_wakes_during_the_rescan(
    hass: HomeAssistant, health_store: HealthStore, mock_connection: MagicMock
) -> None:
    """The rescan awaits history and a store write, and its own state sweep runs
    before that — so a device can start answering mid-scan and still come back
    holding an active signal. The live gate has to run again afterwards."""
    device, entity_id = _speaker(hass, supports_remove=True)
    await _record_offline(health_store, entity_id, device.id, 9 * 86400)

    async def _scan(trigger_audit: bool = True) -> None:
        # Reconnects while the scan is in flight; the signal it already collected
        # stays active, so only a fresh state read can catch this.
        hass.states.async_set(entity_id, "playing")

    monitor = MagicMock()
    monitor.async_request_scan = AsyncMock(side_effect=_scan)
    hass.data.setdefault(DOMAIN, {})["e1"] = {
        "health_store": health_store,
        "insights_engine": MagicMock(),
        "health_monitor": monitor,
    }

    msg = {"id": 1, "type": "selora_ai/insights/delete_device", "device_id": device.id}
    await _delete_device_handler(hass, mock_connection, msg)

    assert mock_connection.send_error.call_args[0][1] == "device_responding"
    assert dr.async_get(hass).async_get(device.id) is not None


@pytest.mark.asyncio
async def test_delete_device_command_refuses_a_just_dark_device(
    hass: HomeAssistant, health_store: HealthStore, mock_connection: MagicMock
) -> None:
    """The endpoint's own duration gate reads the whole-device outage too, so a
    month-old entity signal can't authorize deleting a device that went dark ten
    minutes ago — even if a stale card offered it."""
    hass.data.setdefault(DOMAIN, {})["e1"] = {
        "health_store": health_store,
        "insights_engine": MagicMock(),
    }
    device, _ = await _stale_entity_plus_fresh_outage(hass, health_store)

    msg = {"id": 1, "type": "selora_ai/insights/delete_device", "device_id": device.id}
    await _delete_device_handler(hass, mock_connection, msg)

    assert mock_connection.send_error.call_args[0][1] == "not_offline"
    assert dr.async_get(hass).async_get(device.id) is not None


@pytest.mark.asyncio
async def test_delete_device_command_refuses_an_unremovable_device(
    hass: HomeAssistant, health_store: HealthStore, mock_connection: MagicMock
) -> None:
    hass.data.setdefault(DOMAIN, {})["e1"] = {
        "health_store": health_store,
        "insights_engine": MagicMock(),
    }
    device, entity_id = _speaker(hass, supports_remove=False)
    await _record_offline(health_store, entity_id, device.id, 30 * 86400)

    msg = {"id": 1, "type": "selora_ai/insights/delete_device", "device_id": device.id}
    await _delete_device_handler(hass, mock_connection, msg)

    assert mock_connection.send_error.call_args[0][1] == "not_removable"
    assert dr.async_get(hass).async_get(device.id) is not None


@pytest.mark.asyncio
async def test_delete_device_command_rejects_unknown_device(
    hass: HomeAssistant, mock_connection: MagicMock
) -> None:
    msg = {"id": 1, "type": "selora_ai/insights/delete_device", "device_id": "nope"}
    await _delete_device_handler(hass, mock_connection, msg)
    assert mock_connection.send_error.call_args[0][1] == "unknown_device"


@pytest.mark.asyncio
async def test_delete_device_command_requires_admin(hass: HomeAssistant) -> None:
    conn = MagicMock()
    conn.user.is_admin = False
    msg = {"id": 1, "type": "selora_ai/insights/delete_device", "device_id": "x"}
    await _delete_device_handler(hass, conn, msg)
    assert conn.send_error.call_args[0][1] == "admin_required"
