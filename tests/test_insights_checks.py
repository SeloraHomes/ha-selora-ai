"""Tests for the deterministic Insights check catalog.

Every check is a pure rule over HA state / registries / automations.yaml, so
each test builds a fixture home and asserts the EXACT findings — no LLM, no
flakiness. This is the harness the future health grade rests on.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

from homeassistant.core import HomeAssistant
import pytest
import yaml as pyyaml

from custom_components.selora_ai.const import DOMAIN
from custom_components.selora_ai.health_store import HealthStore
from custom_components.selora_ai.insights_audit import AuditRunner
from custom_components.selora_ai.insights_checks import (
    CHECKS,
    async_run_checks,
    band_for,
    flatten_findings,
    score_from_severities,
)

from .conftest import MockStore


@pytest.fixture(autouse=True)
def _clean_automations_yaml(hass):
    """Each test starts with no automations.yaml so a file written by an earlier
    test can't leak into another (the hass config_dir is reused)."""
    path = Path(hass.config.config_dir) / "automations.yaml"
    if path.exists():
        path.unlink()
    yield


def _write_automations_yaml(hass: HomeAssistant, autos: list[dict[str, Any]]) -> None:
    path = Path(hass.config.config_dir) / "automations.yaml"
    path.write_text(pyyaml.safe_dump(autos), encoding="utf-8")


async def _findings_by_check(hass: HomeAssistant) -> dict[str, dict[str, Any]]:
    """Run checks and index the flattened findings by their check_id."""
    findings = flatten_findings(await async_run_checks(hass))
    return {f["check_id"]: f for f in findings}


@pytest.mark.asyncio
async def test_duplicate_automations(hass: HomeAssistant) -> None:
    """Two automations with an identical trigger+action fingerprint → one
    duplicate finding linking both automation entities."""
    trig = [{"platform": "state", "entity_id": "binary_sensor.door", "to": "on"}]
    act = [{"service": "light.turn_on", "target": {"entity_id": "light.porch"}}]
    _write_automations_yaml(
        hass,
        [
            {"id": "a1", "alias": "Porch A", "trigger": trig, "action": act},
            {"id": "a2", "alias": "Porch B", "trigger": trig, "action": act},
        ],
    )
    hass.states.async_set("automation.porch_a", "on", {"id": "a1", "friendly_name": "Porch A"})
    hass.states.async_set("automation.porch_b", "on", {"id": "a2", "friendly_name": "Porch B"})
    # Referenced entities exist → the broken-automation check stays quiet.
    hass.states.async_set("binary_sensor.door", "off")
    hass.states.async_set("light.porch", "off")

    findings = await _findings_by_check(hass)
    assert "duplicate_automations" in findings
    dup = findings["duplicate_automations"]
    assert dup["severity"] == "warning"
    assert set(dup["entities"]) == {"automation.porch_a", "automation.porch_b"}


@pytest.mark.asyncio
async def test_malformed_yaml_entries_dont_disable_checks(hass: HomeAssistant) -> None:
    """A non-dict entry in automations.yaml (a hand-edit slip) must not crash the
    automation checks and silence duplicate/broken detection for the whole home."""
    trig = [{"platform": "state", "entity_id": "binary_sensor.door", "to": "on"}]
    act = [{"service": "light.turn_on", "target": {"entity_id": "light.porch"}}]
    _write_automations_yaml(
        hass,
        [
            "a stray string",  # non-dict → would crash auto.get(...)
            42,
            {"alias": "Porch A", "trigger": trig, "action": act},
            {"alias": "Porch B", "trigger": trig, "action": act},
        ],
    )
    hass.states.async_set("binary_sensor.door", "off")
    hass.states.async_set("light.porch", "off")

    findings = await _findings_by_check(hass)
    # Bad entries skipped; the two valid duplicates are still reported.
    assert "duplicate_automations" in findings


@pytest.mark.asyncio
async def test_duplicate_id_less_yaml_automations(hass: HomeAssistant) -> None:
    """Byte-identical YAML automations that omit `id` (so they have no entity
    mapping) are still flagged as duplicates — the finding just carries fewer
    entity links."""
    trig = [{"platform": "state", "entity_id": "binary_sensor.door", "to": "on"}]
    act = [{"service": "light.turn_on", "target": {"entity_id": "light.porch"}}]
    _write_automations_yaml(
        hass,
        [
            {"alias": "Porch Left", "trigger": trig, "action": act},  # no id
            {"alias": "Porch Right", "trigger": trig, "action": act},  # no id, same fp
        ],
    )
    # Referenced entities exist → the broken-automation check stays quiet.
    hass.states.async_set("binary_sensor.door", "off")
    hass.states.async_set("light.porch", "off")

    findings = await _findings_by_check(hass)
    assert "duplicate_automations" in findings
    dup = findings["duplicate_automations"]
    assert "Porch Left" in dup["detail"]
    assert "Porch Right" in dup["detail"]


@pytest.mark.asyncio
async def test_no_duplicate_when_actions_differ(hass: HomeAssistant) -> None:
    """Same trigger, different action → NOT a duplicate."""
    trig = [{"platform": "state", "entity_id": "binary_sensor.door", "to": "on"}]
    _write_automations_yaml(
        hass,
        [
            {
                "id": "a1",
                "alias": "A",
                "trigger": trig,
                "action": [{"service": "light.turn_on", "target": {"entity_id": "light.a"}}],
            },
            {
                "id": "a2",
                "alias": "B",
                "trigger": trig,
                "action": [{"service": "light.turn_on", "target": {"entity_id": "light.b"}}],
            },
        ],
    )
    hass.states.async_set("automation.a", "on", {"id": "a1"})
    hass.states.async_set("automation.b", "on", {"id": "a2"})
    for eid in ("binary_sensor.door", "light.a", "light.b"):
        hass.states.async_set(eid, "off")

    assert "duplicate_automations" not in await _findings_by_check(hass)


@pytest.mark.asyncio
async def test_broken_automation_references_missing_entity(hass: HomeAssistant) -> None:
    """An automation referencing a nonexistent entity → broken finding pointing
    at the automation itself."""
    _write_automations_yaml(
        hass,
        [
            {
                "id": "b1",
                "alias": "Ghost light",
                "trigger": [{"platform": "state", "entity_id": "binary_sensor.motion", "to": "on"}],
                "action": [{"service": "light.turn_on", "target": {"entity_id": "light.ghost"}}],
            }
        ],
    )
    hass.states.async_set(
        "automation.ghost_light", "on", {"id": "b1", "friendly_name": "Ghost light"}
    )
    hass.states.async_set("binary_sensor.motion", "off")
    # light.ghost is never set and not in the registry → missing.

    findings = await _findings_by_check(hass)
    assert "broken_automations" in findings
    broken = findings["broken_automations"]
    assert broken["entities"] == ["automation.ghost_light"]
    assert "light.ghost" in broken["detail"]


@pytest.mark.asyncio
async def test_duplicate_automations_by_name(hass: HomeAssistant) -> None:
    """Automations sharing a friendly name are flagged even when their configs
    differ (the real-world case: several 'Doorbell Announcement' automations)."""
    hass.states.async_set(
        "automation.doorbell_1", "on", {"id": "d1", "friendly_name": "Doorbell Announcement"}
    )
    hass.states.async_set(
        "automation.doorbell_2", "on", {"id": "d2", "friendly_name": "Doorbell Announcement"}
    )
    hass.states.async_set(
        "automation.doorbell_3", "on", {"id": "d3", "friendly_name": "Doorbell Announcement"}
    )
    findings = await _findings_by_check(hass)
    assert "duplicate_automations" in findings
    dup = findings["duplicate_automations"]
    assert set(dup["entities"]) == {
        "automation.doorbell_1",
        "automation.doorbell_2",
        "automation.doorbell_3",
    }


@pytest.mark.asyncio
async def test_updates_available(hass: HomeAssistant) -> None:
    """An ``update`` entity that is 'on' → an updates-available finding."""
    hass.states.async_set("update.router", "on", {"friendly_name": "Router Firmware"})
    hass.states.async_set("update.hub", "off", {"friendly_name": "Hub"})  # up to date
    findings = await _findings_by_check(hass)
    assert "updates_available" in findings
    assert findings["updates_available"]["entities"] == ["update.router"]


@pytest.mark.asyncio
async def test_healthy_home_has_no_findings(hass: HomeAssistant) -> None:
    """A home with nothing wrong produces zero findings — but every check still
    ran and reports 'clear' (so the checklist shows the full assessment)."""
    hass.states.async_set("automation.fresh", "on", {"id": "f1"})  # never-run but new
    hass.states.async_set("update.hub", "off")
    results = await async_run_checks(hass)
    assert flatten_findings(results) == []
    assert {r["check_id"] for r in results} == {c.id for c in CHECKS}
    assert all(r["status"] == "clear" for r in results)


@pytest.mark.asyncio
async def test_checklist_reports_every_check(hass: HomeAssistant) -> None:
    """async_run_checks returns one result per registered check, in order, each
    with a status — this is what the page renders as the checklist."""
    hass.states.async_set("update.router", "on", {"friendly_name": "Router"})
    results = await async_run_checks(hass)
    assert [r["check_id"] for r in results] == [c.id for c in CHECKS]
    by_id = {r["check_id"]: r for r in results}
    assert by_id["updates_available"]["status"] == "issues"
    assert by_id["duplicate_automations"]["status"] == "clear"
    # Every result carries the user-facing title + kind for the checklist row.
    assert all(r["title"] and r["kind"] for r in results)


@pytest.mark.asyncio
async def test_findings_sorted_by_severity(hass: HomeAssistant) -> None:
    """Warnings (broken/duplicate) sort ahead of info (updates)."""
    _write_automations_yaml(
        hass,
        [
            {
                "id": "b1",
                "alias": "Broken",
                "trigger": [{"platform": "state", "entity_id": "binary_sensor.x", "to": "on"}],
                "action": [{"service": "light.turn_on", "target": {"entity_id": "light.ghost"}}],
            }
        ],
    )
    hass.states.async_set("automation.broken", "on", {"id": "b1"})
    hass.states.async_set("binary_sensor.x", "off")
    hass.states.async_set("update.router", "on")

    findings = flatten_findings(await async_run_checks(hass))
    severities = [f["severity"] for f in findings]
    assert severities == sorted(
        severities, key=lambda s: {"critical": 0, "warning": 1, "info": 2}[s]
    )
    assert findings[0]["check_id"] == "broken_automations"  # warning before info


@pytest.fixture
def health_store(hass):
    with patch("custom_components.selora_ai.health_store.Store") as mock_cls:
        mock_cls.return_value = MockStore()
        hs = HealthStore(hass)
        hs._store = MockStore()
        yield hs


@pytest.mark.asyncio
async def test_audit_runner_returns_deterministic_findings(
    hass: HomeAssistant, health_store
) -> None:
    """With the LLM audit off (default), AuditRunner.async_run surfaces the
    deterministic check findings as recommendations — no LLM required."""
    hass.states.async_set("update.router", "on", {"friendly_name": "Router"})
    runner = AuditRunner(hass, health_store)
    record = await runner.async_run()
    assert record["status"] == "ok"
    check_ids = {r.get("check_id") for r in record["recommendations"]}
    assert "updates_available" in check_ids


@pytest.mark.asyncio
async def test_device_health_checks_read_layer1_signals(hass: HomeAssistant, health_store) -> None:
    """Device health is part of the check catalog: Layer-1 signals surface as
    per-kind check rows (offline / low battery / integration errors / …)."""
    hass.data.setdefault(DOMAIN, {})["e1"] = {"health_store": health_store}
    await health_store.record_signal(
        kind="unavailable",
        target="media_player.sonos",
        target_kind="entity",
        severity="warning",
        evidence={},
        device_id="dev_sonos",
    )
    await health_store.record_signal(
        kind="battery_low",
        target="sensor.door_battery",
        target_kind="entity",
        severity="warning",
        evidence={"battery_level": 8},
    )
    await health_store.record_signal(
        kind="integration_error",
        target="spotify",
        target_kind="integration",
        severity="critical",
        evidence={"reason": "setup_error"},
    )
    results = {r["check_id"]: r for r in await async_run_checks(hass)}
    assert results["offline_devices"]["status"] == "issues"
    assert results["low_batteries"]["status"] == "issues"
    assert results["integration_errors"]["status"] == "issues"
    assert results["unresponsive_sensors"]["status"] == "clear"
    assert results["unstable_devices"]["status"] == "clear"
    # Integration finding carries a Settings deep-link.
    assert results["integration_errors"]["findings"][0]["link"].endswith("spotify")


@pytest.mark.asyncio
async def test_offline_finding_lists_only_primary_entity(hass: HomeAssistant, health_store) -> None:
    """An offline multi-entity device (Sonos) surfaces one card listing only its
    primary entity (the media_player) — not the config entities (bass/crossfade/
    …) that also go unavailable but are just noise on the card."""
    from homeassistant.const import EntityCategory
    from homeassistant.helpers import device_registry as dr
    from homeassistant.helpers import entity_registry as er
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    hass.data.setdefault(DOMAIN, {})["e1"] = {"health_store": health_store}
    entry = MockConfigEntry(domain="sonos", entry_id="sonos_e", title="Sonos")
    entry.add_to_hass(hass)
    dev_reg = dr.async_get(hass)
    ent_reg = er.async_get(hass)
    speaker = dev_reg.async_get_or_create(
        config_entry_id="sonos_e", identifiers={("sonos", "bedroom")}, name="Bedroom"
    )
    media = ent_reg.async_get_or_create(
        "media_player", "sonos", "bedroom", device_id=speaker.id, config_entry=entry
    )
    config_ids = []
    for domain, obj in (("switch", "crossfade"), ("switch", "tv_autoplay"), ("number", "bass")):
        cfg = ent_reg.async_get_or_create(
            domain,
            "sonos",
            obj,
            device_id=speaker.id,
            config_entry=entry,
            entity_category=EntityCategory.CONFIG,
        )
        config_ids.append(cfg.entity_id)
    # The monitor flags every unavailable entity of the offline device.
    for eid in [media.entity_id, *config_ids]:
        await health_store.record_signal(
            kind="unavailable",
            target=eid,
            target_kind="entity",
            severity="warning",
            evidence={},
            device_id=speaker.id,
        )

    results = {r["check_id"]: r for r in await async_run_checks(hass)}
    offline = results["offline_devices"]
    assert offline["status"] == "issues"
    assert len(offline["findings"]) == 1
    finding = offline["findings"][0]
    assert finding["title"] == "Bedroom is offline"
    assert finding["entities"] == [media.entity_id]  # only the primary, not config


def test_score_from_severities_and_band() -> None:
    """Deterministic penalty roll-up: 100 = clean; severity drives it down."""
    assert score_from_severities([]) == 100
    assert band_for(100) == "A"
    assert score_from_severities(["warning"]) == 95  # first warning, full -5
    assert score_from_severities(["critical", "warning", "info"]) == 79  # -15-5-1
    # Criticals do NOT diminish — each is independently serious.
    assert band_for(score_from_severities(["critical"] * 3)) == "F"  # 100-45=55 < 60


def test_score_diminishing_returns_on_warnings() -> None:
    """Warnings/info get geometric diminishing returns (decay 0.6) so a long
    tail of minor issues can't tank the score and one more barely moves it."""
    # 2nd warning counts 0.6× → -5, -3.
    assert score_from_severities(["warning", "warning"]) == 92
    # A home with 1 integration error + 6 minor warnings lands ~73, not 55,
    # and a 7th warning barely changes it (stable, not a cliff).
    six = score_from_severities(["critical"] + ["warning"] * 6)
    seven = score_from_severities(["critical"] + ["warning"] * 7)
    assert six == 73
    assert abs(seven - six) <= 1


@pytest.mark.asyncio
async def test_audit_record_carries_score_and_band(hass: HomeAssistant, health_store) -> None:
    """The audit record exposes a deterministic score + band rolled up from all
    findings (device health + automation checks)."""
    hass.data.setdefault(DOMAIN, {})["e1"] = {"health_store": health_store}
    await health_store.record_signal(
        kind="integration_error",
        target="spotify",
        target_kind="integration",
        severity="critical",
        evidence={},
    )
    record = await AuditRunner(hass, health_store).async_run()
    assert record["score"] == 85  # one critical -> 100 - 15
    assert record["band"] == "B"


@pytest.mark.asyncio
async def test_failed_check_reported_as_error_not_clear(hass: HomeAssistant, monkeypatch) -> None:
    """A check that raises is reported status='error', never 'clear' — a crashed
    check must not read as assessed-and-healthy (and add no score penalty)."""
    from custom_components.selora_ai import insights_checks as ic

    def _boom(_ctx):
        raise RuntimeError("check exploded")

    monkeypatch.setattr(ic, "CHECKS", [ic.Check("boom", "Boom", ic.KIND_DETERMINISTIC, _boom)])
    results = await ic.async_run_checks(hass)
    assert len(results) == 1
    assert results[0]["status"] == "error"  # NOT "clear"
    assert results[0]["findings"] == []
