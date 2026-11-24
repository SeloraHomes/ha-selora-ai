"""A media device that is off or asleep is not an outage.

Google Cast (and most media integrations) report a TV or speaker that is off as
``unavailable``. On a small home two of them used to cost 45.5 points — band F,
and an installer incident after two audits. These tests drive the real monitor
scan and audit over a small Cast home.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock, patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.selora_ai.const import DOMAIN
from custom_components.selora_ai.health_events import NOTIFY_CHECKS
from custom_components.selora_ai.health_monitor import HealthMonitor
from custom_components.selora_ai.health_store import HealthStore
from custom_components.selora_ai.insights import InsightsEngine
from custom_components.selora_ai.insights_audit import AuditRunner
from custom_components.selora_ai.insights_checks import score_from_findings

from .conftest import MockStore


@pytest.fixture
def health_store(hass: HomeAssistant) -> Iterator[HealthStore]:
    with patch("custom_components.selora_ai.health_store.Store") as mock_cls:
        mock_cls.return_value = MockStore()
        hs = HealthStore(hass)
        hs._store = MockStore()
        hass.data.setdefault(DOMAIN, {})["e1"] = {"health_store": hs}
        yield hs


@pytest.fixture(autouse=True)
def _no_grace(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "custom_components.selora_ai.health_monitor.HEALTH_UNAVAILABLE_GRACE_SECS", 0
    )


def _device(
    hass: HomeAssistant, platform: str, uid: str, name: str, domain: str, state: str
) -> tuple[str, str]:
    """A one-entity device on ``platform``; returns (device_id, entity_id)."""
    entry_id = f"{platform}_e"
    if hass.config_entries.async_get_entry(entry_id) is None:
        MockConfigEntry(domain=platform, entry_id=entry_id, title=platform).add_to_hass(hass)
    entry = hass.config_entries.async_get_entry(entry_id)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry_id, identifiers={(platform, uid)}, name=name
    )
    ent = er.async_get(hass).async_get_or_create(
        domain, platform, uid, device_id=device.id, config_entry=entry
    )
    hass.states.async_set(ent.entity_id, state)
    return device.id, ent.entity_id


def _small_home(hass: HomeAssistant) -> dict[str, tuple[str, str]]:
    """Six devices: a Cast TV and a Cast speaker, both off, and four lights."""
    home = {
        "tv": _device(hass, "cast", "vfd43m", "Living Room TV", "media_player", "unavailable"),
        "speaker": _device(hass, "cast", "wiim", "WiiM Sound Lite", "media_player", "unavailable"),
    }
    for i in range(4):
        home[f"light{i}"] = _device(hass, "hue", f"l{i}", f"Lamp {i}", "light", "off")
    return home


async def _unavailable(health_store: HealthStore) -> dict[str, dict[str, Any]]:
    sigs = await health_store.get_signals(status="active", kind="unavailable")
    return {s["target"]: s for s in sigs}


async def _audit(hass: HomeAssistant, health_store: HealthStore) -> dict[str, Any]:
    return await AuditRunner(hass, health_store).async_run()


def _by_check(record: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for f in record["recommendations"]:
        out.setdefault(f["check_id"], []).append(f)
    return out


@pytest.mark.asyncio
async def test_standby_cast_tv_and_speaker_are_not_offline(
    hass: HomeAssistant, health_store: HealthStore
) -> None:
    home = _small_home(hass)
    await HealthMonitor(hass, health_store).async_scan(trigger_audit=False)

    sigs = await _unavailable(health_store)
    for key in ("tv", "speaker"):
        sig = sigs[home[key][1]]
        # Still recorded (an always-on speaker that dropped is worth a line),
        # but as standby at info severity.
        assert sig["severity"] == "info"
        assert sig["evidence"]["standby"] is True

    record = await _audit(hass, health_store)
    checks = _by_check(record)
    assert "offline_devices" not in checks
    # Off for minutes, not a day: not even listed.
    assert "standby_media" not in checks
    assert record["score"] == 100
    assert record["band"] == "A"


@pytest.mark.asyncio
async def test_dead_light_on_the_same_small_home_is_still_penalised(
    hass: HomeAssistant, health_store: HealthStore
) -> None:
    home = _small_home(hass)
    hass.states.async_set(home["light0"][1], "unavailable")
    await HealthMonitor(hass, health_store).async_scan(trigger_audit=False)

    sig = (await _unavailable(health_store))[home["light0"][1]]
    assert sig["severity"] == "warning"
    assert "standby" not in sig["evidence"]

    record = await _audit(hass, health_store)
    offline = _by_check(record)["offline_devices"]
    assert [f["device_id"] for f in offline] == [home["light0"][0]]
    assert offline[0]["severity"] == "warning"
    # 1 of 6 devices: 12 points (the small-home cap), not 72*sqrt(1/6) = 29.4.
    assert record["score_breakdown"]["fleet"]["size"] == 6
    assert record["score"] == 88
    assert record["band"] == "B"
    sections = {s["check_id"]: s for s in record["score_breakdown"]["sections"]}
    assert set(sections) == {"offline_devices"}


@pytest.mark.asyncio
async def test_standby_survives_restart_and_is_listed_after_a_day(
    hass: HomeAssistant, health_store: HealthStore
) -> None:
    """After a Core restart the monitor has no availability history, and the
    stored signal may predate this rule (warning, no standby flag). The first
    scan must still classify the TV as standby, from the registry alone."""
    home = _small_home(hass)
    tv_device, tv = home["tv"]
    await health_store.record_signal(
        kind="unavailable",
        target=tv,
        target_kind="entity",
        severity="warning",
        evidence={"unavailable_seconds": 60},
        device_id=tv_device,
    )
    data = await health_store._get_loaded_data()
    sid = next(iter(data["signals"]))
    data["signals"][sid]["first_seen"] = (datetime.now(UTC) - timedelta(days=3)).isoformat()

    fresh = HealthMonitor(hass, health_store)  # a restart: no in-memory tracks
    assert not fresh._tracks
    await fresh.async_scan(trigger_audit=False)

    sig = (await _unavailable(health_store))[tv]
    assert sig["severity"] == "info"
    assert sig["evidence"]["standby"] is True

    record = await _audit(hass, health_store)
    checks = _by_check(record)
    assert "offline_devices" not in checks
    standby = checks["standby_media"]
    assert [f["device_id"] for f in standby] == [tv_device]
    assert standby[0]["severity"] == "info"
    assert standby[0]["title"] == "Living Room TV is off"
    assert "3 days" in standby[0]["detail"]
    # Listed, but free.
    assert record["score"] == 100
    assert record["score_breakdown"]["sections"] == []


@pytest.mark.asyncio
async def test_media_device_auxiliary_entities_share_the_standby_verdict(
    hass: HomeAssistant, health_store: HealthStore
) -> None:
    """A TV with a remote and a source sensor beside its media_player: every
    primary entity is down, so the device is down (``_device_unreachable``), and
    every one of them is standby, not just the media_player."""
    MockConfigEntry(domain="webostv", entry_id="lg_e", title="LG").add_to_hass(hass)
    entry = hass.config_entries.async_get_entry("lg_e")
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id="lg_e", identifiers={("webostv", "lg")}, name="Bedroom TV"
    )
    ent_reg = er.async_get(hass)
    ids = [
        ent_reg.async_get_or_create(d, "webostv", d, device_id=device.id, config_entry=entry)
        for d in ("media_player", "remote", "sensor")
    ]
    for e in ids:
        hass.states.async_set(e.entity_id, "unavailable")

    await HealthMonitor(hass, health_store).async_scan(trigger_audit=False)
    sigs = await _unavailable(health_store)
    media, _remote, sensor = ids  # remote is outside the scanned domains
    assert set(sigs) == {media.entity_id, sensor.entity_id}
    for sig in sigs.values():
        assert sig["severity"] == "info"
        assert sig["evidence"]["standby"] is True


@pytest.mark.asyncio
async def test_vanished_sonos_stays_standby(hass: HomeAssistant, health_store: HealthStore) -> None:
    """Sonos rediscovery churn drops the state object; ``_keep_vanished_offline``
    keeps the signal off the registry entry, and it keeps the standby verdict."""
    MockConfigEntry(domain="sonos", entry_id="sonos_e", title="Sonos").add_to_hass(hass)
    entry = hass.config_entries.async_get_entry("sonos_e")
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id="sonos_e", identifiers={("sonos", "den")}, name="Den"
    )
    ent_reg = er.async_get(hass)
    media = ent_reg.async_get_or_create(
        "media_player", "sonos", "den", device_id=device.id, config_entry=entry
    )
    ent_reg.async_get_or_create(
        "number",
        "sonos",
        "bass",
        device_id=device.id,
        config_entry=entry,
        entity_category=EntityCategory.CONFIG,
    )
    await health_store.record_signal(
        kind="unavailable",
        target=media.entity_id,
        target_kind="entity",
        severity="info",
        evidence={"unavailable_seconds": 3600, "standby": True},
        device_id=device.id,
    )
    assert hass.states.get(media.entity_id) is None

    loaded = MagicMock()
    loaded.state = ConfigEntryState.LOADED
    with patch.object(hass.config_entries, "async_get_entry", return_value=loaded):
        await HealthMonitor(hass, health_store).async_scan(trigger_audit=False)
    sig = (await _unavailable(health_store))[media.entity_id]
    assert sig["severity"] == "info"
    assert sig["evidence"]["standby"] is True


def test_standby_media_is_neither_scored_nor_announced() -> None:
    assert "standby_media" not in NOTIFY_CHECKS
    standby = {
        "check_id": "standby_media",
        "severity": "info",
        "device_id": "tv",
        "entities": ["media_player.tv"],
    }
    assert score_from_findings([standby, standby], total_devices=5) == 100


def test_small_home_offline_curve() -> None:
    """How offline warning devices alone move a small home across bands."""

    def offline(n: int) -> list[dict[str, Any]]:
        return [
            {"check_id": "offline_devices", "severity": "warning", "device_id": f"d{i}"}
            for i in range(n)
        ]

    # The reported home's fleet: two offline cost 45.5 (54, F) before the cap.
    assert [score_from_findings(offline(n), total_devices=5) for n in (1, 2, 3)] == [86, 71, 57]
    assert [score_from_findings(offline(n), total_devices=10) for n in (1, 2, 3)] == [88, 76, 64]
    # From 18 devices up the sqrt curve is untouched.
    assert [score_from_findings(offline(n), total_devices=18) for n in (2, 3)] == [76, 71]
    assert score_from_findings(offline(1), total_devices=119) == 93
    # A home gone wholly dark still loses everything the fleet family can take.
    assert score_from_findings(offline(1), total_devices=1) == 28
    assert score_from_findings(offline(5), total_devices=5) == 28


class _NoPatterns:
    async def get_suggestions(self, status: str | None = None) -> list[dict[str, Any]]:
        return []


@pytest.mark.asyncio
async def test_insights_skip_a_standby_tv_for_its_first_day(
    hass: HomeAssistant, health_store: HealthStore
) -> None:
    """The panel's insights list (and the export's ``insights``) is built from
    every active signal; a TV switched off for the evening must not read as
    "is unavailable … check its power"."""
    home = _small_home(hass)
    tv = home["tv"][1]
    await HealthMonitor(hass, health_store).async_scan(trigger_audit=False)
    assert tv in await _unavailable(health_store)

    engine = InsightsEngine(hass, health_store, _NoPatterns())
    assert await engine.async_get_insights() == []

    # A day later it is listed, as off, at info.
    data = await health_store._get_loaded_data()
    for sig in data["signals"].values():
        if sig["target"] == tv:
            sig["evidence"]["unavailable_seconds"] = 2 * 86400
    insights = await engine.async_get_insights()
    by_signal = {i["linked_signals"][0]: i for i in insights}
    tv_insight = by_signal[f"unavailable:{tv}"]
    assert tv_insight["title"].endswith("is off")
    assert tv_insight["severity"] == "info"
    assert "2 days" in tv_insight["detail"]
    assert "unavailable" not in tv_insight["detail"]


@pytest.mark.asyncio
async def test_voice_satellite_that_drops_is_still_offline(
    hass: HomeAssistant, health_store: HealthStore
) -> None:
    """A Voice PE exposes a media_player, but it is meant to stay listening:
    dropping off the network is an outage, not standby."""
    MockConfigEntry(domain="esphome", entry_id="vpe_e", title="Voice PE").add_to_hass(hass)
    entry = hass.config_entries.async_get_entry("vpe_e")
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id="vpe_e", identifiers={("esphome", "vpe")}, name="Kitchen Voice"
    )
    ent_reg = er.async_get(hass)
    media = ent_reg.async_get_or_create(
        "media_player", "esphome", "vpe_media", device_id=device.id, config_entry=entry
    )
    satellite = ent_reg.async_get_or_create(
        "assist_satellite", "esphome", "vpe_sat", device_id=device.id, config_entry=entry
    )
    hass.states.async_set(media.entity_id, "unavailable")
    hass.states.async_set(satellite.entity_id, "unavailable")

    await HealthMonitor(hass, health_store).async_scan(trigger_audit=False)
    sig = (await _unavailable(health_store))[media.entity_id]
    assert sig["severity"] == "warning"
    assert "standby" not in sig["evidence"]

    record = await _audit(hass, health_store)
    assert [f["device_id"] for f in _by_check(record)["offline_devices"]] == [device.id]


@pytest.mark.asyncio
async def test_wall_tablet_that_drops_is_still_offline(
    hass: HomeAssistant, health_store: HealthStore
) -> None:
    """A Fully Kiosk tablet exposes a media_player next to its camera: it is
    meant to stay on the wall, so losing it is an outage, not standby."""
    MockConfigEntry(domain="fully_kiosk", entry_id="fk_e", title="Hall tablet").add_to_hass(hass)
    entry = hass.config_entries.async_get_entry("fk_e")
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id="fk_e", identifiers={("fully_kiosk", "tab")}, name="Hall tablet"
    )
    ent_reg = er.async_get(hass)
    media = ent_reg.async_get_or_create(
        "media_player", "fully_kiosk", "tab_media", device_id=device.id, config_entry=entry
    )
    camera = ent_reg.async_get_or_create(
        "camera", "fully_kiosk", "tab_cam", device_id=device.id, config_entry=entry
    )
    hass.states.async_set(media.entity_id, "unavailable")
    hass.states.async_set(camera.entity_id, "unavailable")

    await HealthMonitor(hass, health_store).async_scan(trigger_audit=False)
    sig = (await _unavailable(health_store))[media.entity_id]
    assert sig["severity"] == "warning"
    assert "standby" not in sig["evidence"]
