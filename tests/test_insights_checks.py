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
    score_breakdown_from_findings,
    score_from_findings,
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
async def test_integration_error_finding_surfaces_reason(hass: HomeAssistant, health_store) -> None:
    """The captured failure reason lands in the finding detail — so the card
    (and the exported insight) say what went wrong, not just that it did."""
    hass.data.setdefault(DOMAIN, {})["e1"] = {"health_store": health_store}
    await health_store.record_signal(
        kind="integration_error",
        target="reolink",
        target_kind="integration",
        severity="critical",
        evidence={"source": "config_entry", "reason": "Timeout connecting to 192.168.1.5"},
    )
    results = {r["check_id"]: r for r in await async_run_checks(hass)}
    detail = results["integration_errors"]["findings"][0]["detail"]
    assert "Timeout connecting to 192.168.1.5" in detail


@pytest.mark.asyncio
async def test_integration_error_finding_surfaces_repair_issue_text(
    hass: HomeAssistant, health_store
) -> None:
    """When only a repair issue's rendered text is available, that text carries
    into the finding detail."""
    hass.data.setdefault(DOMAIN, {})["e1"] = {"health_store": health_store}
    await health_store.record_signal(
        kind="integration_error",
        target="zwave_js",
        target_kind="integration",
        severity="critical",
        evidence={
            "source": "repair_issue",
            "issue_id": "invalid_server_version",
            "issue_title": "Z-Wave JS server is outdated",
            "issue_description": "Update the add-on to continue",
        },
    )
    results = {r["check_id"]: r for r in await async_run_checks(hass)}
    detail = results["integration_errors"]["findings"][0]["detail"]
    assert "Z-Wave JS server is outdated" in detail
    assert "Update the add-on to continue" in detail


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


def _offline(n: int) -> list[dict[str, str]]:
    """N per-device offline findings (each one affected device)."""
    return [{"check_id": "offline_devices", "severity": "warning"} for _ in range(n)]


def test_score_scales_with_fleet_fraction() -> None:
    """The whole point of the fleet-fraction model: the score MOVES with how
    many devices are down, instead of saturating at ~88 forever."""
    # Clean fleet.
    assert score_from_findings([], total_devices=119) == 100
    # A big chunk of the fleet down (33/119 ≈ 28%) lands in the D band —
    # NOT the old stuck-at-88 that count-insensitive decay produced.
    heavy = score_from_findings(_offline(33), total_devices=119)
    assert 55 <= heavy <= 66, heavy
    assert band_for(heavy) in ("D", "F")
    # A handful down in the same fleet is a mild hit (A band).
    light = score_from_findings(_offline(3), total_devices=119)
    assert light >= 88
    # More devices down => strictly lower score (it actually tracks the count).
    assert score_from_findings(_offline(20), total_devices=119) < light


def test_score_fleet_fraction_normalizes_to_home_size() -> None:
    """The same absolute count hurts a small home more than a large one."""
    small = score_from_findings(_offline(5), total_devices=10)
    large = score_from_findings(_offline(5), total_devices=200)
    assert small < large
    # A fully-dark fleet bottoms out in the F band.
    assert band_for(score_from_findings(_offline(50), total_devices=50)) == "F"


def test_score_non_device_checks_stay_fixed_severity() -> None:
    """Integration errors / hygiene are NOT fleet-scaled — a lone critical is a
    fixed -15 regardless of (even zero) fleet size, so it can't explode."""
    findings = [{"check_id": "integration_errors", "severity": "critical"}]
    assert score_from_findings(findings, total_devices=0) == 85
    assert score_from_findings(findings, total_devices=500) == 85


def _fleet_finding(
    check_id: str,
    *,
    severity: str = "warning",
    device_id: str | None = None,
    entities: list[str] | None = None,
) -> dict[str, Any]:
    """A per-device fleet finding with an explicit affected target."""
    return {
        "check_id": check_id,
        "severity": severity,
        "device_id": device_id,
        "entities": entities or [],
    }


def test_score_dedups_fleet_findings_by_device() -> None:
    """One physical device flagged by several fleet checks counts once, at its
    worst severity — not once per finding (the review's 1/10 vs 2/10 case)."""
    both = [
        _fleet_finding("low_batteries", device_id="dev1"),
        _fleet_finding("unstable_devices", device_id="dev1"),
    ]
    one = [_fleet_finding("low_batteries", device_id="dev1")]
    # Same device, two findings scores identically to that device flagged once.
    assert score_from_findings(both, total_devices=10) == score_from_findings(one, total_devices=10)
    # 1/10 affected -> 77 (the review's expected value), NOT 2/10 -> 68.
    assert score_from_findings(both, total_devices=10) == 77
    # Two DIFFERENT devices are genuinely 2/10 affected -> 68.
    two = [
        _fleet_finding("low_batteries", device_id="dev1"),
        _fleet_finding("unstable_devices", device_id="dev2"),
    ]
    assert score_from_findings(two, total_devices=10) == 68


def test_score_dedup_keeps_worst_severity() -> None:
    """Deduping a device keeps its most-severe finding, not the last seen."""
    warn_then_crit = [
        _fleet_finding("low_batteries", severity="warning", device_id="dev1"),
        _fleet_finding("offline_devices", severity="critical", device_id="dev1"),
    ]
    crit_only = [_fleet_finding("offline_devices", severity="critical", device_id="dev1")]
    assert score_from_findings(warn_then_crit, total_devices=10) == score_from_findings(
        crit_only, total_devices=10
    )


def test_score_registry_less_entity_is_a_single_warning() -> None:
    """A registry-less (legacy YAML / custom-integration) offline entity in a
    home with no registered devices reads as one warning, not a fully-down
    fleet divided by the fallback denominator of 1 (the review's P1 case)."""
    findings = [_fleet_finding("offline_devices", entities=["light.legacy"])]
    score = score_from_findings(findings, total_devices=0)
    assert score == 95  # -5 fixed-severity warning, NOT the -72 fleet floor (28)
    assert band_for(score) == "A"
    # Same standalone entity flagged by two fleet checks is still one warning.
    dup = [
        _fleet_finding("offline_devices", entities=["light.legacy"]),
        _fleet_finding("unstable_devices", entities=["light.legacy"]),
    ]
    assert score_from_findings(dup, total_devices=0) == 95


def test_score_stale_device_id_scored_as_standalone() -> None:
    """A finding whose device was deleted/disabled since its signal was raised is
    no longer in the active fleet — scoring it as a slice of that fleet would
    divide by a fleet it has left. It's scored as a standalone finding instead
    (the review's P2 case: one stale finding must not wipe the whole score)."""
    findings = [_fleet_finding("offline_devices", device_id="ghost", entities=["light.ghost"])]
    # No active devices remain and the finding's device isn't in the set:
    # a single warning (-5 -> 95), NOT a 1/1 fleet wipeout (-72 -> 28).
    assert score_from_findings(findings, total_devices=0, active_device_ids=set()) == 95
    # Still in the active fleet -> genuine 1/1 outage.
    assert score_from_findings(findings, total_devices=1, active_device_ids={"ghost"}) == 28
    # Without an active-id set the device_id is trusted (back-compat path).
    assert score_from_findings(findings, total_devices=1) == 28


def test_score_breakdown_explains_the_number() -> None:
    """The breakdown decomposes the score per finding: family totals + rows that
    sum to the penalty, biggest first, and a ``score`` matching the scalar API."""
    findings = [
        _fleet_finding("offline_devices", device_id="dev1", severity="warning"),
        _fleet_finding("low_batteries", device_id="dev1", severity="warning"),  # same device
        _fleet_finding("offline_devices", device_id="dev2", severity="critical"),
        {"check_id": "integration_errors", "severity": "critical", "entities": []},
    ]
    active = {"dev1", "dev2"}
    bd = score_breakdown_from_findings(findings, total_devices=10, active_device_ids=active)

    # Score agrees with the scalar entry point.
    assert bd["score"] == score_from_findings(findings, total_devices=10, active_device_ids=active)
    # dev1 flagged twice → one affected device; dev2 → the other. 2 of 10.
    assert bd["fleet"] == {"affected": 2, "size": 10, "fraction": pytest.approx(0.4, abs=0.05)}
    # One fleet row per affected DEVICE (deduped), plus the integration finding.
    fleet_rows = [c for c in bd["contributions"] if c["family"] == "fleet"]
    other_rows = [c for c in bd["contributions"] if c["family"] == "other"]
    assert {c["target"] for c in fleet_rows} == {"dev1", "dev2"}
    assert [c["check_id"] for c in other_rows] == ["integration_errors"]
    # Sections roll the per-finding rows up per check, titled like the checklist.
    sections = {s["check_id"]: s for s in bd["sections"]}
    assert sections["offline_devices"]["title"] == "Devices offline"
    assert sections["offline_devices"]["count"] == 2  # dev1 + dev2, deduped
    assert sections["offline_devices"]["family"] == "fleet"
    # A section's points equal its findings' points, and sections sort by impact.
    assert bd["sections"] == sorted(bd["sections"], key=lambda s: s["points"], reverse=True)
    assert sections["integration_errors"]["count"] == 1
    # Rows are sorted biggest-impact-first and sum (≈) to the total penalty.
    pts = [c["points"] for c in bd["contributions"]]
    assert pts == sorted(pts, reverse=True)
    assert sum(pts) == pytest.approx(bd["device_penalty"] + bd["other_penalty"], abs=0.3)
    # The scalar score reflects roughly the full penalty (stored totals are
    # rounded to 1 dp, so allow the ±1 that rounding can introduce).
    assert abs(bd["score"] - (100 - bd["device_penalty"] - bd["other_penalty"])) <= 1


def test_score_breakdown_caps_deductions_at_zero_score() -> None:
    """A penalty over 100 (many criticals) floors the score at 0 — the reported
    deductions scale down to the 100 points actually removed, so the breakdown
    reconciles instead of "explaining" a 0 with >100 points of loss."""
    findings = [
        {"check_id": "integration_errors", "severity": "critical", "entities": []}
        for _ in range(7)
    ]
    bd = score_breakdown_from_findings(findings, total_devices=10)
    # 7 * 15 = 105 raw penalty → the score floors at 0.
    assert bd["score"] == 0
    # Family totals + per-finding rows + sections all sum to ~100 (the amount
    # actually removed), never the uncapped 105.
    assert bd["device_penalty"] + bd["other_penalty"] == pytest.approx(100, abs=0.5)
    assert sum(c["points"] for c in bd["contributions"]) == pytest.approx(100, abs=0.5)
    assert sum(s["points"] for s in bd["sections"]) == pytest.approx(100, abs=0.5)
    # Per-finding stamps scaled in step (each 15 * 100/105 ≈ 14.3).
    assert all(f["score_points"] == pytest.approx(14.3, abs=0.2) for f in findings)


def test_score_breakdown_section_reconciles_with_score() -> None:
    """A fleet penalty split across many devices must sum back to the removed
    points — rounding each share early produced a 70-pt section for a 72-pt
    penalty. The section total has to reconcile with 100 - score."""
    findings = [
        _fleet_finding("offline_devices", device_id=f"dev{i}", severity="warning")
        for i in range(50)
    ]
    bd = score_breakdown_from_findings(findings, total_devices=50)
    section = next(s for s in bd["sections"] if s["check_id"] == "offline_devices")
    assert section["points"] == pytest.approx(100 - bd["score"], abs=0.6)


def test_score_breakdown_survives_huge_fleet() -> None:
    """A very large fleet gives each device a tiny share; the old per-finding
    rounding dropped every 0.036 to 0 and produced no breakdown at all despite a
    real penalty. Precision must be kept until the section totals are formed."""
    findings = [
        _fleet_finding("offline_devices", device_id=f"dev{i}", severity="warning")
        for i in range(2000)
    ]
    bd = score_breakdown_from_findings(findings, total_devices=2000)
    assert bd["score"] < 100
    sections = [s for s in bd["sections"] if s["points"] > 0]
    assert sections, "a real penalty must still produce a breakdown at scale"
    assert sections[0]["points"] == pytest.approx(100 - bd["score"], abs=1.0)


def test_score_breakdown_stamps_points_on_each_finding() -> None:
    """Every contributing finding is stamped with its own ``score_points`` so the
    panel maps a card to its penalty directly. Several id-less findings from one
    check (no device_id, no entities → empty target) must keep DISTINCT points —
    the diminishing −5 / −3 / −1.8, not all the same first value."""
    findings = [
        {"check_id": "duplicate_automations", "severity": "warning", "title": "Dup A"},
        {"check_id": "duplicate_automations", "severity": "warning", "title": "Dup B"},
        {"check_id": "duplicate_automations", "severity": "warning", "title": "Dup C"},
    ]
    score_breakdown_from_findings(findings, total_devices=10)
    stamped = [f["score_points"] for f in findings]
    # Diminishing returns: 5 * 0.6**0, 5 * 0.6**1, 5 * 0.6**2 → 5.0, 3.0, 1.8.
    assert stamped == [5.0, 3.0, 1.8]
    # And they're distinct — the bug was all three showing the first value.
    assert len(set(stamped)) == 3


def test_score_breakdown_clean_home_is_empty() -> None:
    """A healthy home: full marks and nothing to explain."""
    bd = score_breakdown_from_findings([], total_devices=50)
    assert bd["score"] == 100
    assert bd["contributions"] == []
    assert bd["sections"] == []
    assert bd["device_penalty"] == 0.0 and bd["other_penalty"] == 0.0


def test_score_mixed_active_and_stale_devices() -> None:
    """Only findings for devices in the active set feed the fleet fraction; a
    stale one rides the fixed-severity tail without inflating the numerator."""
    findings = [
        _fleet_finding("offline_devices", device_id="live1"),
        _fleet_finding("offline_devices", device_id="stale"),  # not in active set
    ]
    active = score_from_findings(findings, total_devices=10, active_device_ids={"live1"})
    # 1/10 active outage (77) minus one standalone warning on the tail — strictly
    # below a clean 1/10, but nowhere near counting the stale device as 2/10 (68).
    assert active < 77
    assert active > 68


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
async def test_active_device_count_excludes_inactive(hass: HomeAssistant, health_store) -> None:
    """The fleet denominator counts only enabled devices with a live entity —
    user-disabled devices and retained-but-empty devices are excluded so they
    can't dilute the fleet-fraction penalty (the review's P2 case)."""
    from homeassistant.helpers import device_registry as dr
    from homeassistant.helpers import entity_registry as er
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    entry = MockConfigEntry(domain="demo", entry_id="fleet_e", title="Demo")
    entry.add_to_hass(hass)
    dev_reg = dr.async_get(hass)
    ent_reg = er.async_get(hass)

    # 1 active device (enabled, with an enabled entity backed by a state).
    active = dev_reg.async_get_or_create(
        config_entry_id="fleet_e", identifiers={("demo", "active")}, name="Active"
    )
    active_ent = ent_reg.async_get_or_create(
        "light", "demo", "active", device_id=active.id, config_entry=entry
    )
    hass.states.async_set(active_ent.entity_id, "on")
    # A user-disabled device — cannot raise a finding.
    dev_reg.async_get_or_create(
        config_entry_id="fleet_e",
        identifiers={("demo", "off")},
        name="Disabled",
        disabled_by=dr.DeviceEntryDisabler.USER,
    )
    # A retained device with no entities at all.
    dev_reg.async_get_or_create(
        config_entry_id="fleet_e", identifiers={("demo", "empty")}, name="Empty"
    )
    # A device whose only entity is disabled — not participating.
    only_disabled = dev_reg.async_get_or_create(
        config_entry_id="fleet_e", identifiers={("demo", "dis_ent")}, name="DisabledEntity"
    )
    ent_reg.async_get_or_create(
        "light",
        "demo",
        "dis_ent",
        device_id=only_disabled.id,
        config_entry=entry,
        disabled_by=er.RegistryEntryDisabler.USER,
    )
    # A retired device whose enabled entity lingers in the registry but has no
    # state object — the health monitor scans states, so it can never flag it.
    orphaned = dev_reg.async_get_or_create(
        config_entry_id="fleet_e", identifiers={("demo", "orphan")}, name="Orphan"
    )
    ent_reg.async_get_or_create(
        "light", "demo", "orphan", device_id=orphaned.id, config_entry=entry
    )  # deliberately no hass.states.async_set(...)

    # 5 devices in the registry, but only 1 is an active, participating device.
    assert len(dev_reg.devices) == 5
    assert AuditRunner(hass, health_store)._active_device_ids([]) == {active.id}


@pytest.mark.asyncio
async def test_active_device_count_excludes_selora_muted(hass: HomeAssistant, health_store) -> None:
    """A device whose every enabled entity is muted via the Selora exclude label
    can never raise a finding, so it's dropped from the fleet denominator — a
    device with a live, non-muted entity still counts (the review's P2 case)."""
    from homeassistant.helpers import (
        device_registry as dr,
    )
    from homeassistant.helpers import (
        entity_registry as er,
    )
    from homeassistant.helpers import (
        label_registry as lr,
    )
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.selora_ai.const import SELORA_EXCLUDE_LABEL_NAME

    entry = MockConfigEntry(domain="demo", entry_id="mute_e", title="Demo")
    entry.add_to_hass(hass)
    label = lr.async_get(hass).async_create(name=SELORA_EXCLUDE_LABEL_NAME)
    dev_reg = dr.async_get(hass)
    ent_reg = er.async_get(hass)

    # Participating device: one live, non-muted entity backed by a state.
    live = dev_reg.async_get_or_create(
        config_entry_id="mute_e", identifiers={("demo", "live")}, name="Live"
    )
    live_ent = ent_reg.async_get_or_create(
        "light", "demo", "live", device_id=live.id, config_entry=entry
    )
    hass.states.async_set(live_ent.entity_id, "on")

    # Fully-muted device: its only enabled entity carries the exclude label.
    muted = dev_reg.async_get_or_create(
        config_entry_id="mute_e", identifiers={("demo", "muted")}, name="Muted"
    )
    muted_ent = ent_reg.async_get_or_create(
        "light", "demo", "muted", device_id=muted.id, config_entry=entry
    )
    ent_reg.async_update_entity(muted_ent.entity_id, labels={label.label_id})

    # Device muted at the device level (label on the device, not the entity).
    dev_muted = dev_reg.async_get_or_create(
        config_entry_id="mute_e", identifiers={("demo", "dev_muted")}, name="DeviceMuted"
    )
    dev_reg.async_update_device(dev_muted.id, labels={label.label_id})
    ent_reg.async_get_or_create(
        "light", "demo", "dev_muted", device_id=dev_muted.id, config_entry=entry
    )

    # 3 registered devices, but only the one live device can raise a finding.
    assert len(dev_reg.devices) == 3
    assert AuditRunner(hass, health_store)._active_device_ids([]) == {live.id}


@pytest.mark.asyncio
async def test_active_device_ids_keeps_preserved_offline(hass: HomeAssistant, health_store) -> None:
    """A preserved-offline device (its state objects briefly vanished during
    rediscovery churn, but its outage signal is kept alive off the still-enabled
    registry entry) has a real finding and no state. It must stay in the fleet so
    a sole outage scores as 1/1 (28), not a standalone warning (95). (review P1)."""
    from homeassistant.config_entries import ConfigEntryState
    from homeassistant.helpers import device_registry as dr
    from homeassistant.helpers import entity_registry as er
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    # A dependency-free domain: HA imports the integration on unload during
    # teardown, and a real one (e.g. sonos) would need its optional deps (soco).
    entry = MockConfigEntry(
        domain="demo", entry_id="churn_e", title="Rediscovery", state=ConfigEntryState.LOADED
    )
    entry.add_to_hass(hass)
    dev_reg = dr.async_get(hass)
    ent_reg = er.async_get(hass)

    device = dev_reg.async_get_or_create(
        config_entry_id="churn_e", identifiers={("demo", "living")}, name="Living Room"
    )
    ent = ent_reg.async_get_or_create(
        "media_player", "demo", "living", device_id=device.id, config_entry=entry
    )  # enabled registry entry, integration LOADED, but NO state object (vanished)

    finding = _fleet_finding("offline_devices", device_id=device.id, entities=[ent.entity_id])
    runner = AuditRunner(hass, health_store)
    active = runner._active_device_ids([finding])
    assert active == {device.id}  # preserved offline device stays in the fleet
    # Scored as a genuine 1/1 outage, not a lone standalone warning.
    assert score_from_findings([finding], len(active), active) == 28


@pytest.mark.asyncio
async def test_active_device_ids_drops_stale_finding_for_empty_device(
    hass: HomeAssistant, health_store
) -> None:
    """A signal that outlived removal/disabling of its entity, on a device whose
    registry entry lingers enabled, must NOT be promoted to the active fleet —
    it's a retained-but-empty shell, not a preserved-offline device. The stale
    warning stays standalone (score 95), never a 1/1 outage (28). (review P2)."""
    from homeassistant.config_entries import ConfigEntryState
    from homeassistant.helpers import device_registry as dr
    from homeassistant.helpers import entity_registry as er
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    entry = MockConfigEntry(
        domain="demo", entry_id="stale_e", title="Demo", state=ConfigEntryState.LOADED
    )
    entry.add_to_hass(hass)
    dev_reg = dr.async_get(hass)
    ent_reg = er.async_get(hass)

    # Device kept in the registry, but its entity is gone (never registered):
    # the finding's entity has no registry entry, so it isn't preserved-offline.
    device = dev_reg.async_get_or_create(
        config_entry_id="stale_e", identifiers={("demo", "ghost")}, name="Ghost"
    )
    finding = _fleet_finding(
        "offline_devices", device_id=device.id, entities=["media_player.removed"]
    )
    runner = AuditRunner(hass, health_store)
    active = runner._active_device_ids([finding])
    assert active == set()  # stale finding does NOT resurrect the empty device
    assert score_from_findings([finding], len(active), active) == 95

    # A disabled entity is likewise not preserved-offline, even with a state.
    disabled_ent = ent_reg.async_get_or_create(
        "media_player",
        "demo",
        "disabled",
        device_id=device.id,
        config_entry=entry,
        disabled_by=er.RegistryEntryDisabler.USER,
    )
    hass.states.async_set(disabled_ent.entity_id, "unavailable")
    finding2 = _fleet_finding(
        "offline_devices", device_id=device.id, entities=[disabled_ent.entity_id]
    )
    assert runner._active_device_ids([finding2]) == set()


@pytest.mark.asyncio
async def test_active_device_ids_only_promotes_offline_findings(
    hass: HomeAssistant, health_store
) -> None:
    """Only `offline_devices` signals are preserved through state loss. A stale
    battery / silent / unstable finding whose entity briefly lost its state must
    NOT be promoted — those detectors resolve on the next scan — so it scores as a
    standalone warning (95), not a 1/1 fleet outage (28). (review P2)."""
    from homeassistant.config_entries import ConfigEntryState
    from homeassistant.helpers import device_registry as dr
    from homeassistant.helpers import entity_registry as er
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    entry = MockConfigEntry(
        domain="demo", entry_id="np_e", title="Demo", state=ConfigEntryState.LOADED
    )
    entry.add_to_hass(hass)
    dev_reg = dr.async_get(hass)
    ent_reg = er.async_get(hass)

    # A genuine preserved-offline entity (enabled, no state, integration loaded):
    # its device WOULD be promoted for an offline finding, proving the finding
    # kind — not the entity state — is what gates the other detectors out.
    device = dev_reg.async_get_or_create(
        config_entry_id="np_e", identifiers={("demo", "churn")}, name="Churn"
    )
    ent = ent_reg.async_get_or_create(
        "sensor", "demo", "churn", device_id=device.id, config_entry=entry
    )  # enabled, integration loaded, but NO state right now
    runner = AuditRunner(hass, health_store)

    for stale_check in ("low_batteries", "unresponsive_sensors", "unstable_devices"):
        finding = _fleet_finding(stale_check, device_id=device.id, entities=[ent.entity_id])
        active = runner._active_device_ids([finding])
        assert active == set(), stale_check  # not preserved → not in the fleet
        assert score_from_findings([finding], len(active), active) == 95, stale_check

    # The very same entity IS promoted for an offline finding (the preserved kind).
    offline = _fleet_finding("offline_devices", device_id=device.id, entities=[ent.entity_id])
    active = runner._active_device_ids([offline])
    assert active == {device.id}
    assert score_from_findings([offline], len(active), active) == 28


@pytest.mark.asyncio
async def test_active_device_ids_excludes_transient(hass: HomeAssistant, health_store) -> None:
    """Transient (BLE/presence) and out-of-scope devices carry live states but
    can never raise a fleet finding — they must not inflate the denominator and
    under-penalize a real outage (the review's P2 case)."""
    from homeassistant.helpers import device_registry as dr
    from homeassistant.helpers import entity_registry as er
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    entry = MockConfigEntry(domain="demo", entry_id="tr_e", title="Demo")
    entry.add_to_hass(hass)
    dev_reg = dr.async_get(hass)
    ent_reg = er.async_get(hass)

    # Real, scannable device.
    real = dev_reg.async_get_or_create(
        config_entry_id="tr_e", identifiers={("demo", "real")}, name="Real"
    )
    real_ent = ent_reg.async_get_or_create(
        "light", "demo", "real", device_id=real.id, config_entry=entry
    )
    hass.states.async_set(real_ent.entity_id, "on")

    # Presence device_tracker — transient (away/out-of-range is expected).
    presence = dev_reg.async_get_or_create(
        config_entry_id="tr_e", identifiers={("demo", "phone")}, name="Phone"
    )
    presence_ent = ent_reg.async_get_or_create(
        "device_tracker", "demo", "phone", device_id=presence.id, config_entry=entry
    )
    hass.states.async_set(presence_ent.entity_id, "home")

    # Out-of-scope device: its only entity is an `update` (not a tracked domain).
    updater = dev_reg.async_get_or_create(
        config_entry_id="tr_e", identifiers={("demo", "fw")}, name="Firmware"
    )
    update_ent = ent_reg.async_get_or_create(
        "update", "demo", "fw", device_id=updater.id, config_entry=entry
    )
    hass.states.async_set(update_ent.entity_id, "off")

    # 3 devices with live states, but only the scannable one is in the fleet.
    assert len(dev_reg.devices) == 3
    assert AuditRunner(hass, health_store)._active_device_ids([]) == {real.id}


@pytest.mark.asyncio
async def test_active_device_ids_includes_out_of_scope_battery(
    hass: HomeAssistant, health_store
) -> None:
    """The battery detector is intentionally all-domain, so a device whose only
    battery-reporting entity is outside COLLECTOR_DOMAINS can still be flagged.
    Those healthy devices must be in the denominator too, else ten of them with
    one low battery score as a 1/1 outage (28) instead of 1/10 (77). (review P1)."""
    from homeassistant.helpers import device_registry as dr
    from homeassistant.helpers import entity_registry as er
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    entry = MockConfigEntry(domain="demo", entry_id="batt_e", title="Demo")
    entry.add_to_hass(hass)
    dev_reg = dr.async_get(hass)
    ent_reg = er.async_get(hass)

    # 10 out-of-scope devices (siren ∉ COLLECTOR_DOMAINS) whose only entity
    # reports a battery level. One is low; the rest are healthy.
    ids: list[str] = []
    for i in range(10):
        device = dev_reg.async_get_or_create(
            config_entry_id="batt_e", identifiers={("demo", f"batt{i}")}, name=f"Siren {i}"
        )
        siren = ent_reg.async_get_or_create(
            "siren", "demo", f"batt{i}", device_id=device.id, config_entry=entry
        )
        hass.states.async_set(siren.entity_id, "off", {"battery_level": 5 if i == 0 else 90})
        ids.append(device.id)

    runner = AuditRunner(hass, health_store)
    # All ten healthy out-of-scope battery devices are in the fleet, findings or not.
    assert runner._active_device_ids([]) == set(ids)
    # One low-battery finding scores as 1/10 (77), not a 1/1 wipeout (28).
    finding = _fleet_finding("low_batteries", device_id=ids[0])
    active = runner._active_device_ids([finding])
    assert active == set(ids)
    assert score_from_findings([finding], len(active), active) == 77


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
