"""Tests for the home roster builder — the "what's running, what's not" export."""

from __future__ import annotations

from datetime import UTC, datetime
import json

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.selora_ai.insights_roster import build_home_roster


@pytest.mark.asyncio
async def test_roster_captures_running_and_not_running(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(domain="hue", entry_id="hue_entry", title="Philips Hue")
    entry.add_to_hass(hass)

    dev_reg = dr.async_get(hass)
    ent_reg = er.async_get(hass)

    device = dev_reg.async_get_or_create(
        config_entry_id="hue_entry",
        identifiers={("hue", "lamp-1")},
        name="Living Room Lamp",
        manufacturer="Signify",
        model="LCT001",
    )

    ent_reg.async_get_or_create(
        "light",
        "hue",
        "lamp1",
        device_id=device.id,
        config_entry=entry,
        suggested_object_id="living_room",
    )
    ent_reg.async_get_or_create(
        "light",
        "hue",
        "lamp2",
        device_id=device.id,
        config_entry=entry,
        suggested_object_id="bedroom",
    )
    hass.states.async_set("light.living_room", "on")
    hass.states.async_set("light.bedroom", "unavailable")

    roster = build_home_roster(hass)

    # Integration is present and loaded, with its device/entity counts.
    hue = next(i for i in roster["integrations"] if i["domain"] == "hue")
    assert hue["title"] == "Philips Hue"
    assert hue["devices"] == 1
    assert hue["entities"] == 2

    # Device rollup counts the one unavailable entity.
    dev_row = next(d for d in roster["devices"] if d["id"] == device.id)
    assert dev_row["entities"] == 2
    assert dev_row["unavailable_entities"] == 1
    assert dev_row["manufacturer"] == "Signify"
    assert dev_row["transient"] is False  # a real Hue device, not a BLE advert

    # Entities carry running/not-running state + their device_id.
    ents = {e["entity_id"]: e for e in roster["entities"]}
    assert ents["light.living_room"]["available"] is True
    assert ents["light.bedroom"]["available"] is False
    assert ents["light.living_room"]["device_id"] == device.id
    assert not roster["truncated"]


@pytest.mark.asyncio
async def test_roster_flags_custom_integrations(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(domain="my_custom", entry_id="c1", title="My Custom")
    entry.add_to_hass(hass)
    core = MockConfigEntry(domain="hue", entry_id="h1", title="Hue")
    core.add_to_hass(hass)

    roster = build_home_roster(hass, custom_domains={"my_custom"})
    by_domain = {i["domain"]: i for i in roster["integrations"]}
    assert by_domain["my_custom"]["custom"] is True
    assert by_domain["hue"]["custom"] is False

    # No custom_domains passed → everything defaults to False.
    roster2 = build_home_roster(hass)
    assert all(i["custom"] is False for i in roster2["integrations"])


@pytest.mark.asyncio
async def test_roster_flags_transient_ble_devices(hass: HomeAssistant) -> None:
    """BLE-beacon / presence-advert devices (out-of-range != broken) are flagged
    transient so consumers can drop them from the "needs attention" tally, while
    real devices stay transient=False."""
    ble = MockConfigEntry(domain="private_ble_device", entry_id="ble1", title="Phone Tag")
    ble.add_to_hass(hass)
    hue = MockConfigEntry(domain="hue", entry_id="h1", title="Hue")
    hue.add_to_hass(hass)

    dev_reg = dr.async_get(hass)
    beacon = dev_reg.async_get_or_create(
        config_entry_id="ble1",
        identifiers={("private_ble_device", "AA:BB:CC:DD:EE:FF")},
        name="S19ef93db0f79b545C",
    )
    lamp = dev_reg.async_get_or_create(
        config_entry_id="h1",
        identifiers={("hue", "lamp-1")},
        name="Living Room Lamp",
    )

    roster = build_home_roster(hass)
    by_id = {d["id"]: d for d in roster["devices"]}
    assert by_id[beacon.id]["transient"] is True
    assert by_id[lamp.id]["transient"] is False


@pytest.mark.asyncio
async def test_roster_merged_device_not_transient_if_any_real(hass: HomeAssistant) -> None:
    """A device merged across config entries is transient only when EVERY backing
    integration is transient. A real integration in the mix keeps it visible,
    deterministically — regardless of which entry is primary or iteration order."""
    ble = MockConfigEntry(domain="private_ble_device", entry_id="ble1", title="BLE")
    ble.add_to_hass(hass)
    hue = MockConfigEntry(domain="hue", entry_id="h1", title="Hue")
    hue.add_to_hass(hass)
    ble2 = MockConfigEntry(domain="bermuda", entry_id="ble2", title="Bermuda")
    ble2.add_to_hass(hass)

    dev_reg = dr.async_get(hass)
    # Real device (hue) later also picked up by a BLE presence integration.
    merged = dev_reg.async_get_or_create(
        config_entry_id="h1",
        identifiers={("hue", "lamp-1")},
        name="Living Room Lamp",
    )
    merged = dev_reg.async_update_device(merged.id, add_config_entry_id="ble1")
    # Device backed only by transient integrations stays transient.
    all_ble = dev_reg.async_get_or_create(
        config_entry_id="ble1",
        identifiers={("private_ble_device", "AA:BB:CC:DD:EE:FF")},
        name="Tag",
    )
    all_ble = dev_reg.async_update_device(all_ble.id, add_config_entry_id="ble2")

    by_id = {d["id"]: d for d in build_home_roster(hass)["devices"]}
    assert by_id[merged.id]["transient"] is False  # hue keeps it visible
    assert by_id[all_ble.id]["transient"] is True  # all-transient stays flagged


@pytest.mark.asyncio
async def test_roster_labels_integration_with_manifest_name(hass: HomeAssistant) -> None:
    """The producer emits the human manifest name so Connect can label an
    integration 'National Weather Service (NWS)' rather than its domain."""
    entry = MockConfigEntry(domain="nws", entry_id="nws1", title="NWS: 37.1, -90.5")
    entry.add_to_hass(hass)

    roster = build_home_roster(hass, integration_names={"nws": "National Weather Service (NWS)"})
    nws = next(i for i in roster["integrations"] if i["domain"] == "nws")
    assert nws["name"] == "National Weather Service (NWS)"
    assert nws["title"] == "NWS: 37.1, -90.5"  # instance title still available

    # Absent map → name falls back to empty (consumer uses title/domain).
    assert (
        next(i for i in build_home_roster(hass)["integrations"] if i["domain"] == "nws")["name"]
        == ""
    )


@pytest.mark.asyncio
async def test_roster_includes_integration_documentation_url(hass: HomeAssistant) -> None:
    """The producer emits each integration's manifest documentation URL so a
    roster consumer can link the app; "" when unknown."""
    entry = MockConfigEntry(domain="hue", entry_id="hue1", title="Hue Bridge")
    entry.add_to_hass(hass)

    roster = build_home_roster(
        hass,
        integration_urls={"hue": "https://www.home-assistant.io/integrations/hue"},
    )
    hue = next(i for i in roster["integrations"] if i["domain"] == "hue")
    assert hue["url"] == "https://www.home-assistant.io/integrations/hue"

    # Absent map → url falls back to empty.
    assert (
        next(i for i in build_home_roster(hass)["integrations"] if i["domain"] == "hue")["url"]
        == ""
    )


@pytest.mark.asyncio
async def test_roster_exposes_integration_version(hass: HomeAssistant) -> None:
    """Each integration carries its manifest version (custom components declare
    one; core integrations are unversioned → "")."""
    custom = MockConfigEntry(domain="hacs", entry_id="hacs1", title="HACS")
    custom.add_to_hass(hass)
    core = MockConfigEntry(domain="hue", entry_id="h1", title="Hue")
    core.add_to_hass(hass)

    roster = build_home_roster(hass, integration_versions={"hacs": "2.0.1"})
    by_domain = {i["domain"]: i for i in roster["integrations"]}
    assert by_domain["hacs"]["version"] == "2.0.1"
    assert by_domain["hue"]["version"] == ""  # unversioned/core → empty

    # Absent map → every version falls back to empty.
    assert all(i["version"] == "" for i in build_home_roster(hass)["integrations"])


@pytest.mark.asyncio
async def test_roster_exposes_supervisor_apps(hass: HomeAssistant) -> None:
    """The Supervisor app (add-on) list is normalized into RosterApp rows —
    version + latest + update availability + state, with URL credentials
    stripped."""
    apps = [
        {
            "slug": "core_mosquitto",
            "name": "Mosquitto broker",
            "version": "6.4.0",
            "version_latest": "6.5.0",
            "update_available": True,
            "state": "started",
            "url": "https://github.com/home-assistant/addons/tree/master/mosquitto",
        },
        {
            "slug": "a0d7b954_nodered",
            "name": "Node-RED",
            "version": "18.0.3",
            "version_latest": "18.0.3",
            "update_available": False,
            "state": "stopped",
            "url": "http://admin:secret@nodered.local/",
        },
    ]

    roster = build_home_roster(hass, apps=apps)
    by_slug = {a["slug"]: a for a in roster["apps"]}

    mqtt = by_slug["core_mosquitto"]
    assert mqtt["name"] == "Mosquitto broker"
    assert mqtt["version"] == "6.4.0"
    assert mqtt["version_latest"] == "6.5.0"
    assert mqtt["update_available"] is True
    assert mqtt["state"] == "started"

    nodered = by_slug["a0d7b954_nodered"]
    assert nodered["update_available"] is False
    assert nodered["state"] == "stopped"
    # Embedded basic-auth credentials are stripped before export.
    assert nodered["url"] == "http://nodered.local/"


@pytest.mark.asyncio
async def test_roster_apps_resolve_installing_store(hass: HomeAssistant) -> None:
    """Each app carries the store it was installed from, resolved from the
    opaque repository slug the Supervisor reports."""
    apps = [
        {"slug": "core_mosquitto", "name": "Mosquitto broker", "repository": "core"},
        {"slug": "a0d7b954_nodered", "name": "Node-RED", "repository": "a0d7b954"},
        {"slug": "local_thing", "name": "Thing", "repository": "local"},
    ]
    repositories = {
        "core": {
            "slug": "core",
            "name": "Official add-ons",
            "source": "core",
            "url": "",
            "maintainer": "Home Assistant",
        },
        "a0d7b954": {
            "slug": "a0d7b954",
            "name": "Home Assistant Community Store",
            "source": "https://github.com/hacs/addons",
            "url": "https://hacs.xyz",
            "maintainer": "HACS <hi@hacs.xyz>",
        },
        "local": {"slug": "local", "name": "Local add-ons", "source": "local", "url": ""},
    }

    by_slug = {
        a["slug"]: a
        for a in build_home_roster(hass, apps=apps, app_repositories=repositories)["apps"]
    }

    community = by_slug["a0d7b954_nodered"]
    assert community["repository_name"] == "Home Assistant Community Store"
    assert community["repository_url"] == "https://hacs.xyz"
    assert community["repository_maintainer"] == "HACS <hi@hacs.xyz>"

    # Built-in stores are exported without a URL so a consumer renders them by
    # name. Note these rows carry url: "" — see the test below for the case
    # where the Supervisor does populate one.
    assert by_slug["core_mosquitto"]["repository_name"] == "Official add-ons"
    assert by_slug["core_mosquitto"]["repository_url"] == ""
    assert by_slug["local_thing"]["repository_url"] == ""
    assert by_slug["local_thing"]["repository_maintainer"] == ""


@pytest.mark.asyncio
async def test_roster_builtin_store_url_is_dropped(hass: HomeAssistant) -> None:
    """A built-in store is identified by slug, not by lacking a URL.

    The Supervisor does populate ``url`` on the core row, so selecting it
    before checking the slug published a link the export contract promises is
    absent (``repository_url`` is "" for core/local — render by name only).
    Guarding only the ``source`` fallback missed this, because ``source`` is
    the literal "core"/"local" and never looked like a URL.
    """
    apps = [
        {"slug": "core_mosquitto", "name": "Mosquitto broker", "repository": "core"},
        {"slug": "local_thing", "name": "Thing", "repository": "local"},
    ]
    repositories = {
        "core": {
            "slug": "core",
            "name": "Official add-ons",
            "source": "core",
            "url": "https://github.com/home-assistant/addons",
            "maintainer": "Home Assistant",
        },
        "local": {
            "slug": "local",
            "name": "Local add-ons",
            "source": "local",
            "url": "https://example.invalid/local",
        },
    }

    by_slug = {
        a["slug"]: a
        for a in build_home_roster(hass, apps=apps, app_repositories=repositories)["apps"]
    }
    assert by_slug["core_mosquitto"]["repository_url"] == ""
    assert by_slug["local_thing"]["repository_url"] == ""
    # The name still resolves — only the link is withheld.
    assert by_slug["core_mosquitto"]["repository_name"] == "Official add-ons"


@pytest.mark.asyncio
async def test_roster_app_store_url_falls_back_to_source(hass: HomeAssistant) -> None:
    """``url`` is optional in a store's repository.yaml — fall back to the git
    URL it was added with, credential-stripped, on any host."""
    apps = [
        {"slug": "x_hydroqc", "name": "Hydroqc", "repository": "b1c2"},
        {"slug": "x_private", "name": "Private", "repository": "d3e4"},
    ]
    repositories = {
        "b1c2": {
            "slug": "b1c2",
            "name": "hydroqc-hass-addons",
            "source": "https://gitlab.com/hydroqc/hydroqc-hass-addons",
            "url": "",
        },
        "d3e4": {
            "slug": "d3e4",
            "name": "Private store",
            "source": "https://user:token@git.example.com/addons",
            "url": "",
        },
    }

    by_slug = {
        a["slug"]: a
        for a in build_home_roster(hass, apps=apps, app_repositories=repositories)["apps"]
    }

    assert (
        by_slug["x_hydroqc"]["repository_url"] == "https://gitlab.com/hydroqc/hydroqc-hass-addons"
    )
    # A private store is added with credentials embedded in its git URL.
    assert by_slug["x_private"]["repository_url"] == "https://git.example.com/addons"


@pytest.mark.asyncio
async def test_roster_apps_default_empty_store(hass: HomeAssistant) -> None:
    """No store list resolved (HA too old for get_store, or the lookup failed)
    → the repository fields are empty, never missing."""
    apps = [{"slug": "core_mosquitto", "name": "Mosquitto broker", "repository": "core"}]

    app = build_home_roster(hass, apps=apps)["apps"][0]
    assert app["repository_name"] == ""
    assert app["repository_url"] == ""
    assert app["repository_maintainer"] == ""


@pytest.mark.asyncio
async def test_roster_apps_default_empty_unsupervised(hass: HomeAssistant) -> None:
    """No apps threaded in (unsupervised Core/Container install) → empty list,
    never missing from the roster."""
    roster = build_home_roster(hass)
    assert roster["apps"] == []


@pytest.mark.asyncio
async def test_roster_unknown_is_available_not_offline(hass: HomeAssistant) -> None:
    """An ``unknown`` entity (e.g. a TTS/notify service) is a valid no-value
    state, not offline — it must count as available."""
    entry = MockConfigEntry(domain="google_translate", entry_id="gt", title="Google Translate")
    entry.add_to_hass(hass)
    ent_reg = er.async_get(hass)
    ent_reg.async_get_or_create(
        "tts", "google_translate", "en_com", config_entry=entry, suggested_object_id="gt_en"
    )
    hass.states.async_set("tts.gt_en", "unknown")

    roster = build_home_roster(hass)
    row = next(e for e in roster["entities"] if e["entity_id"] == "tts.gt_en")
    assert row["available"] is True


@pytest.mark.asyncio
async def test_roster_includes_state_only_entities(hass: HomeAssistant) -> None:
    """Entities with no unique_id live in the state machine but have no
    entity-registry entry (legacy YAML / some custom integrations). They must
    still appear in the full-home roster and its availability totals."""
    # No registry entry created — these exist only in the state machine.
    hass.states.async_set(
        "sensor.legacy_yaml", "42", {"friendly_name": "Legacy YAML", "device_class": "temperature"}
    )
    hass.states.async_set("binary_sensor.legacy_down", "unavailable")

    roster = build_home_roster(hass)
    by_id = {e["entity_id"]: e for e in roster["entities"]}

    assert "sensor.legacy_yaml" in by_id
    row = by_id["sensor.legacy_yaml"]
    assert row["name"] == "Legacy YAML"
    assert row["domain"] == "sensor"
    assert row["device_class"] == "temperature"
    assert row["available"] is True
    assert row["device_id"] is None
    assert row["disabled"] is False

    # The unavailable state-only entity counts toward the broken total.
    assert "binary_sensor.legacy_down" in by_id
    assert by_id["binary_sensor.legacy_down"]["available"] is False
    assert roster["unavailable_total"] >= 1


@pytest.mark.asyncio
async def test_disabled_entities_not_counted_unavailable(hass: HomeAssistant) -> None:
    """Disabled entities are intentionally off, not broken (the NWS case:
    12 of 13 disabled). They must land in disabled_entities, not
    unavailable_entities, so the device reads healthy."""
    entry = MockConfigEntry(domain="nws", entry_id="nws_e", title="NWS")
    entry.add_to_hass(hass)
    dev_reg = dr.async_get(hass)
    ent_reg = er.async_get(hass)
    device = dev_reg.async_get_or_create(
        config_entry_id="nws_e", identifiers={("nws", "loc")}, name="NWS"
    )
    active = ent_reg.async_get_or_create(
        "sensor",
        "nws",
        "kpof",
        device_id=device.id,
        config_entry=entry,
        suggested_object_id="nws_temp",
    )
    for i in range(2):
        ent_reg.async_get_or_create(
            "sensor",
            "nws",
            f"d{i}",
            device_id=device.id,
            config_entry=entry,
            suggested_object_id=f"nws_disabled_{i}",
            disabled_by=er.RegistryEntryDisabler.INTEGRATION,
        )
    hass.states.async_set(active.entity_id, "28")

    roster = build_home_roster(hass)
    row = next(d for d in roster["devices"] if d["id"] == device.id)
    assert row["entities"] == 3
    assert row["unavailable_entities"] == 0  # not broken — just disabled
    assert row["disabled_entities"] == 2
    assert "url" in row  # configuration_url passthrough ("" when none)


@pytest.mark.asyncio
async def test_hidden_unavailable_not_counted(hass: HomeAssistant) -> None:
    """A hidden entity isn't user-facing, so an unavailable hidden entity does
    not count toward the device's unavailable_entities health."""
    entry = MockConfigEntry(domain="demo", entry_id="h_e", title="Demo")
    entry.add_to_hass(hass)
    dev_reg = dr.async_get(hass)
    ent_reg = er.async_get(hass)
    device = dev_reg.async_get_or_create(
        config_entry_id="h_e", identifiers={("demo", "d")}, name="Demo Device"
    )
    hidden = ent_reg.async_get_or_create(
        "sensor",
        "demo",
        "diag",
        device_id=device.id,
        config_entry=entry,
        suggested_object_id="demo_hidden",
        hidden_by=er.RegistryEntryHider.USER,
    )
    hass.states.async_set(hidden.entity_id, "unavailable")

    roster = build_home_roster(hass)
    row = next(d for d in roster["devices"] if d["id"] == device.id)
    assert row["unavailable_entities"] == 0


@pytest.mark.asyncio
async def test_roster_marks_disabled_entity_unavailable(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(domain="demo", entry_id="demo_entry")
    entry.add_to_hass(hass)
    ent_reg = er.async_get(hass)

    ent_reg.async_get_or_create(
        "switch",
        "demo",
        "disabled_switch",
        suggested_object_id="off_switch",
        disabled_by=er.RegistryEntryDisabler.USER,
    )

    roster = build_home_roster(hass)
    row = next(e for e in roster["entities"] if e["entity_id"] == "switch.off_switch")
    assert row["disabled"] is True
    assert row["available"] is False
    assert row["state"] == "disabled"


@pytest.mark.asyncio
async def test_roster_reports_failed_integration(hass: HomeAssistant) -> None:
    from homeassistant.config_entries import ConfigEntryState

    entry = MockConfigEntry(domain="broken", entry_id="broken_entry", title="Broken")
    entry.add_to_hass(hass)
    entry.mock_state(hass, ConfigEntryState.SETUP_RETRY)

    roster = build_home_roster(hass)
    broken = next(i for i in roster["integrations"] if i["domain"] == "broken")
    assert broken["state"] == "setup_retry"


@pytest.mark.asyncio
async def test_roster_attributes_issue_to_affected_integration(hass: HomeAssistant) -> None:
    """A repair raised by one integration on behalf of another (issue_domain)
    marks the AFFECTED integration as having an issue, not the creator."""
    from homeassistant.helpers import issue_registry as ir

    creator = MockConfigEntry(domain="watchman", entry_id="watchman_e", title="Watchman")
    creator.add_to_hass(hass)
    affected = MockConfigEntry(domain="hue", entry_id="hue_e", title="Hue")
    affected.add_to_hass(hass)

    ir.async_create_issue(
        hass,
        "watchman",  # creator
        "hue_broken",
        issue_domain="hue",  # affected integration
        is_fixable=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key="hue_broken",
    )

    roster = build_home_roster(hass)
    integrations = {i["domain"]: i for i in roster["integrations"]}
    assert integrations["hue"]["has_issue"] is True  # affected integration flagged
    assert integrations["watchman"]["has_issue"] is False  # creator not flagged


@pytest.mark.asyncio
async def test_roster_excludes_dismissed_repair(hass: HomeAssistant) -> None:
    """A repair the user dismissed (active but dismissed_version set) is not
    exported as an active problem — matches the health monitor's handling."""
    from homeassistant.helpers import issue_registry as ir

    entry = MockConfigEntry(domain="hue", entry_id="hue_d", title="Hue")
    entry.add_to_hass(hass)
    ir.async_create_issue(
        hass,
        "hue",
        "hue_bad",
        is_fixable=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key="hue_bad",
    )
    ir.async_ignore_issue(hass, "hue", "hue_bad", True)  # user dismisses it

    roster = build_home_roster(hass)
    hue = next(i for i in roster["integrations"] if i["domain"] == "hue")
    assert hue["has_issue"] is False  # dismissed → not reported


@pytest.mark.asyncio
async def test_roster_includes_automation_state(hass: HomeAssistant) -> None:
    hass.states.async_set(
        "automation.morning",
        "on",
        {"friendly_name": "Morning", "id": "abc", "last_triggered": "2026-01-01T06:00:00+00:00"},
    )
    hass.states.async_set(
        "automation.selora_night",
        "off",
        {"friendly_name": "[Selora AI] Night", "id": "selora_ai_night"},
    )

    roster = build_home_roster(hass)
    autos = {a["entity_id"]: a for a in roster["automations"]}
    assert autos["automation.morning"]["enabled"] is True
    assert autos["automation.morning"]["last_triggered"] is not None
    assert autos["automation.selora_night"]["enabled"] is False
    assert autos["automation.selora_night"]["selora"] is True


@pytest.mark.asyncio
async def test_roster_serializes_datetime_last_triggered_as_iso(hass: HomeAssistant) -> None:
    """HA gives last_triggered as a datetime; the roster must emit ISO-8601
    (with a 'T'), not the space-separated str(datetime) that would break the
    envelope schema's date-time format."""
    triggered = datetime(2026, 1, 1, 6, 0, 0, tzinfo=UTC)
    hass.states.async_set(
        "automation.morning", "on", {"friendly_name": "Morning", "last_triggered": triggered}
    )
    hass.states.async_set(
        "script.cleanup", "off", {"friendly_name": "Cleanup", "last_triggered": triggered}
    )

    roster = build_home_roster(hass)
    auto_lt = roster["automations"][0]["last_triggered"]
    script_lt = roster["scripts"][0]["last_triggered"]

    assert auto_lt == "2026-01-01T06:00:00+00:00"
    assert script_lt == "2026-01-01T06:00:00+00:00"
    # The whole roster must be JSON-serializable with no datetime coercion.
    dumped = json.dumps(roster)
    assert "2026-01-01 06:00:00" not in dumped  # no space-separated leak


@pytest.mark.asyncio
async def test_roster_last_triggered_none_and_str_passthrough(hass: HomeAssistant) -> None:
    hass.states.async_set("automation.no_trigger", "on", {"friendly_name": "Never"})
    hass.states.async_set(
        "automation.str_trigger",
        "on",
        {"friendly_name": "Str", "last_triggered": "2026-02-02T08:00:00+00:00"},
    )
    roster = build_home_roster(hass)
    by_name = {a["name"]: a for a in roster["automations"]}
    assert by_name["Never"]["last_triggered"] is None
    assert by_name["Str"]["last_triggered"] == "2026-02-02T08:00:00+00:00"


def test_strip_url_credentials() -> None:
    """Embedded userinfo is removed; host/port/path (incl. IPv6) preserved."""
    from custom_components.selora_ai.insights_roster import _strip_url_credentials

    assert _strip_url_credentials("http://admin:token@192.168.1.5/") == "http://192.168.1.5/"
    assert _strip_url_credentials("https://user:pass@host:8080/p?q=1") == "https://host:8080/p?q=1"
    assert _strip_url_credentials("http://user:pass@[::1]:9000/") == "http://[::1]:9000/"
    assert _strip_url_credentials("http://192.168.1.5/") == "http://192.168.1.5/"  # no creds
    assert _strip_url_credentials("") == ""


@pytest.mark.asyncio
async def test_roster_strips_credentials_from_device_url(hass: HomeAssistant) -> None:
    """A device configuration_url carrying basic-auth creds is sanitized before
    it can leave the box in the export."""
    entry = MockConfigEntry(domain="router", entry_id="r1", title="Router")
    entry.add_to_hass(hass)
    dev_reg = dr.async_get(hass)
    device = dev_reg.async_get_or_create(
        config_entry_id="r1",
        identifiers={("router", "x")},
        name="Router",
        configuration_url="http://admin:secret@192.168.1.1/",
    )

    roster = build_home_roster(hass)
    dev_row = next(d for d in roster["devices"] if d["id"] == device.id)
    assert dev_row["url"] == "http://192.168.1.1/"
    assert "secret" not in dev_row["url"]
