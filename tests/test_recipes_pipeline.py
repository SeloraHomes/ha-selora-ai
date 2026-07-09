"""Tests for the v2 recipes pipeline.

Covers the six stages end-to-end against the two demo recipes that
ship in ``recipes/``. The same recipes are what users get in their
testing wizard, so a passing test here is a strong signal the manual
flow will work.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from custom_components.selora_ai.recipes.const import RECIPE_BUNDLE_DIR
from custom_components.selora_ai.recipes.loader import (
    async_list_bundles,
    async_load_bundle,
)
from custom_components.selora_ai.recipes.manifest import (
    ManifestError,
    RoleSpec,
    _coerce_role,
    load_manifest,
)
from custom_components.selora_ai.recipes.packager import (
    PackagerError,
    _ensure_packages_include,
    _has_packages_include,
    _slug_to_filename,
    ensure_packages_include,
    package_path,
)
from custom_components.selora_ai.recipes.pipeline import (
    async_install,
    async_preview,
    async_uninstall,
)
from custom_components.selora_ai.recipes.renderer import (
    RenderError,
    _build_environment,
    _render_one,
    render_package,
)
from custom_components.selora_ai.recipes.resolver import (
    _entity_satisfies_role,
    resolve,
)
from custom_components.selora_ai.recipes.validator import validate_inputs

# Test recipe fixtures. Integration no longer ships builtin recipes
# (catalog is source of truth); these copies stay here so the pipeline
# tests can exercise the same surfaces without a live network call.
TEST_RECIPE_FIXTURES = Path(__file__).parent / "recipe_fixtures"


# ── Fixtures ────────────────────────────────────────────────────────


def _stage_bundle(hass, slug: str) -> Path:
    """Copy a fixture recipe bundle into the configured bundles dir
    so the loader can find it. Returns the staged path.
    """
    src = TEST_RECIPE_FIXTURES / slug
    if not src.exists():
        raise FileNotFoundError(
            f"No fixture bundle for slug {slug!r} in recipe_fixtures/"
        )
    dest = Path(hass.config.config_dir) / RECIPE_BUNDLE_DIR / slug
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(src, dest)
    return dest


@pytest.fixture
def leak_bundle_dir(hass, tmp_path):
    hass.config.config_dir = str(tmp_path)
    return _stage_bundle(hass, "leak-lockdown")


@pytest.fixture
def bedtime_bundle_dir(hass, tmp_path):
    hass.config.config_dir = str(tmp_path)
    return _stage_bundle(hass, "bedtime-routine")


@pytest.fixture
def tornado_bundle_dir(hass, tmp_path):
    hass.config.config_dir = str(tmp_path)
    return _stage_bundle(hass, "tornado-alert")


@pytest.fixture
def v3_bedtime_bundle_dir(hass, tmp_path):
    """Mount both the v3 demo bundle AND the literal-mode classic
    bedtime bundle so the coexistence test has something to compare."""
    hass.config.config_dir = str(tmp_path)
    _stage_bundle(hass, "bedtime-routine")
    return _stage_bundle(hass, "bedtime-routine-v3")


def _seed_v3_bedtime_home(hass) -> None:
    hass.states.async_set("light.bedroom", "on")
    hass.states.async_set("light.hallway", "on")
    hass.states.async_set("light.spare", "off")
    hass.states.async_set("lock.front_door", "unlocked")
    hass.states.async_set("climate.upstairs", "heat")


_V3_BEDTIME_FULL_SELECTION = {
    "bedroom_lights": ["light.bedroom", "light.hallway"],
    "door_locks": ["lock.front_door"],
    "thermostat": ["climate.upstairs"],
}


def _seed_tornado_home(hass) -> None:
    """Populate hass.states with entities the tornado-alert recipe matches."""
    hass.states.async_set("siren.living_room_siren", "off")
    hass.states.async_set(
        "binary_sensor.fp2_living_room",
        "off",
        {"device_class": "occupancy"},
    )
    hass.states.async_set(
        "binary_sensor.fp2_kitchen",
        "off",
        {"device_class": "occupancy"},
    )
    hass.states.async_set(
        "binary_sensor.front_door",
        "off",
        {"device_class": "door"},
    )
    hass.states.async_set(
        "binary_sensor.back_door",
        "off",
        {"device_class": "door"},
    )
    hass.states.async_set("media_player.kitchen_speaker", "idle")
    hass.states.async_set("media_player.living_room_speaker", "idle")


_TORNADO_FULL_SELECTION = {
    "indoor_siren": ["siren.living_room_siren"],
    "presence_sensors": [
        "binary_sensor.fp2_living_room",
        "binary_sensor.fp2_kitchen",
    ],
    "door_sensors": ["binary_sensor.front_door", "binary_sensor.back_door"],
    "announce_media_players": [
        "media_player.kitchen_speaker",
        "media_player.living_room_speaker",
    ],
}


def _seed_leak_home(hass) -> None:
    """Populate hass.states with everything leak-lockdown needs."""
    hass.states.async_set(
        "binary_sensor.kitchen_leak",
        "off",
        {"device_class": "moisture"},
    )
    hass.states.async_set(
        "binary_sensor.basement_leak",
        "off",
        {"device_class": "moisture"},
    )
    hass.states.async_set("cover.kitchen_valve", "open")
    hass.states.async_set(
        "light.alarm_strip_one",
        "off",
        {"supported_color_modes": ["rgb"]},
    )
    hass.states.async_set(
        "light.alarm_strip_two",
        "off",
        {"supported_color_modes": ["hs"]},
    )
    # An entity the recipe should NOT match (no moisture device_class).
    hass.states.async_set(
        "binary_sensor.door",
        "off",
        {"device_class": "door"},
    )


def _seed_bedtime_home(hass) -> None:
    # The two ``light.bed_light`` / ``light.bedroom_lamp`` entities
    # match the manifest's pinned bindings — seeding both keeps the
    # bedtime tests exercising the full install path. Tests that
    # specifically want to see the pending-pin behaviour seed only
    # one of them.
    hass.states.async_set("light.bed_light", "off")
    hass.states.async_set("light.bedroom_lamp", "off")
    hass.states.async_set("light.bedroom", "on")
    hass.states.async_set("light.hallway", "on")
    hass.states.async_set("lock.front_door", "unlocked")
    hass.states.async_set("climate.upstairs", "heat", {"current_temperature": 21})


# ── Manifest load ───────────────────────────────────────────────────


def test_load_leak_lockdown_manifest(leak_bundle_dir: Path) -> None:
    manifest = load_manifest(leak_bundle_dir)
    assert manifest.slug == "leak-lockdown"
    assert manifest.version == "2.0.0"
    assert {r.id for r in manifest.roles} == {
        "leak_sensors",
        "lockdown_covers",
        "alarm_lights",
    }
    assert {i.id for i in manifest.inputs} == {
        "alarm_brightness",
        "all_clear_delay_minutes",
    }
    # Templates resolved to real files on disk.
    assert len(manifest.package_files) == 2


def test_load_bedtime_manifest(bedtime_bundle_dir: Path) -> None:
    manifest = load_manifest(bedtime_bundle_dir)
    assert manifest.slug == "bedtime-routine"
    assert {i.type for i in manifest.inputs} == {
        "string",
        "number",
        "boolean",
        "select",
    }


def test_manifest_rejects_unknown_role_kind(tmp_path: Path) -> None:
    """Validation catches typos before the pipeline ever runs."""
    bundle = tmp_path / "bad"
    bundle.mkdir()
    (bundle / "package").mkdir()
    (bundle / "package" / "x.yaml.j2").write_text("automation: []\n")
    (bundle / "manifest.yaml").write_text(
        "slug: bad\n"
        "version: 1.0.0\n"
        "title: Bad\n"
        "roles:\n"
        "  - id: weird\n"
        "    kind: bogus_domain\n"
        "package_files:\n"
        "  - package/x.yaml.j2\n"
    )
    with pytest.raises(ManifestError, match="unknown kind"):
        load_manifest(bundle)


def test_manifest_rejects_path_traversal(tmp_path: Path) -> None:
    bundle = tmp_path / "trav"
    bundle.mkdir()
    (bundle / "package").mkdir()
    (bundle / "package" / "real.yaml.j2").write_text("automation: []\n")
    (bundle / "manifest.yaml").write_text(
        "slug: trav\n"
        "version: 1.0.0\n"
        "title: Trav\n"
        "roles: []\n"
        "package_files:\n"
        "  - ../escape.yaml.j2\n"
    )
    with pytest.raises(ManifestError, match=r"\.\."):
        load_manifest(bundle)


# ── Resolver ────────────────────────────────────────────────────────


async def test_resolver_binds_leak_sensors_and_lights(
    hass, leak_bundle_dir: Path
) -> None:
    """Auto-selection role (leak_sensors) binds every match; required
    roles (alarm_lights, lockdown_covers) only bind what we pass in
    ``selections``. The wizard owns picking those; the resolver just
    applies the pick.
    """
    _seed_leak_home(hass)
    bundle = await async_load_bundle(hass, "leak-lockdown")
    report = resolve(
        bundle.manifest,
        hass,
        selections={
            "alarm_lights": [
                "light.alarm_strip_one",
                "light.alarm_strip_two",
            ],
            "lockdown_covers": ["cover.kitchen_valve"],
        },
    )
    assert report.ok, report.failures()
    # leak_sensors is selection:auto — every moisture sensor bound.
    assert set(report.bindings["leak_sensors"]) == {
        "binary_sensor.kitchen_leak",
        "binary_sensor.basement_leak",
    }
    # door binary_sensor has device_class=door — must NOT match.
    assert "binary_sensor.door" not in report.bindings["leak_sensors"]
    # alarm_lights honoured the explicit selection.
    assert set(report.bindings["alarm_lights"]) == {
        "light.alarm_strip_one",
        "light.alarm_strip_two",
    }
    # Candidates are surfaced separately for the wizard's toggle row.
    assert set(report.candidates["alarm_lights"]) == {
        "light.alarm_strip_one",
        "light.alarm_strip_two",
    }


# ── Integration (platform) role filter ──────────────────────────────


@pytest.mark.parametrize(
    "domain",
    [
        "lg_thinq",
        "nws",
        "17track",       # digit prefix — a real HA integration shape
        "3_day_blinds",  # digit prefix + underscores
    ],
)
def test_role_parses_integration_filter(domain) -> None:
    """The optional ``integration`` field round-trips through the loader,
    including valid HA domains that start with a digit, and defaults to
    None when absent."""
    scoped = _coerce_role(
        {"id": "fridge_doors", "kind": "binary_sensor", "device_class": "door", "integration": domain}
    )
    assert scoped.integration == domain
    unscoped = _coerce_role({"id": "any_door", "kind": "binary_sensor", "device_class": "door"})
    assert unscoped.integration is None


@pytest.mark.parametrize(
    "bad",
    [
        "lg thinq!",   # space + punctuation
        "LG_ThinQ",    # uppercase — HA domains are lowercase
        123,           # YAML number (int)
        "123",         # purely numeric — no HA domain is digits-only
        "lg-thinq",    # hyphen is not a domain char
    ],
)
def test_role_rejects_malformed_integration(bad) -> None:
    """A non-domain-shaped integration is caught at manifest load, not
    silently ignored at resolve time. HA domains are lowercase
    ``[a-z0-9_]`` identifiers carrying at least one letter, so uppercase,
    punctuation, and digits-only values are rejected. Digit-prefixed
    domains (17track) are valid and covered by the parses test above."""
    with pytest.raises(ManifestError, match="integration"):
        _coerce_role({"id": "fridge_doors", "kind": "binary_sensor", "integration": bad})


def _fake_registry(platforms: dict[str, str | None]):
    """Fake entity registry: entity_id -> platform. A platform of None
    (or an entity_id not present) means "no registry entry"."""

    def _get(entity_id: str):
        if entity_id not in platforms:
            return None
        return SimpleNamespace(platform=platforms[entity_id], original_device_class="door")

    return SimpleNamespace(async_get=_get)


def test_entity_satisfies_role_integration_filter() -> None:
    """An integration-scoped role matches only entities owned by that
    integration; an unscoped role still matches every door."""
    fridge = SimpleNamespace(
        entity_id="binary_sensor.refrigerator_door", attributes={"device_class": "door"}
    )
    dishwasher = SimpleNamespace(
        entity_id="binary_sensor.dishwasher_door", attributes={"device_class": "door"}
    )
    stick_on = SimpleNamespace(
        entity_id="binary_sensor.myggbett_door", attributes={"device_class": "door"}
    )
    reg = _fake_registry(
        {
            "binary_sensor.refrigerator_door": "lg_thinq",
            "binary_sensor.dishwasher_door": "mqtt",
            # stick_on has no registry entry at all.
        }
    )

    scoped = RoleSpec(
        id="fridge_doors", kind="binary_sensor", device_class="door", integration="lg_thinq"
    )
    assert _entity_satisfies_role(scoped, fridge, reg) is True
    assert _entity_satisfies_role(scoped, dishwasher, reg) is False
    # No registry entry ⇒ no known platform ⇒ can't satisfy a scoped role.
    assert _entity_satisfies_role(scoped, stick_on, reg) is False

    unscoped = RoleSpec(id="any_door", kind="binary_sensor", device_class="door")
    assert _entity_satisfies_role(unscoped, fridge, reg) is True
    assert _entity_satisfies_role(unscoped, dishwasher, reg) is True


def test_role_parses_and_validates_match_filter() -> None:
    """``match`` round-trips and a bad regex is rejected at load."""
    r = _coerce_role(
        {"id": "wf", "kind": "sensor", "match": r"water[ _]filter$"}
    )
    assert r.match == r"water[ _]filter$"
    assert _coerce_role({"id": "x", "kind": "sensor"}).match is None
    with pytest.raises(ManifestError, match="valid regex"):
        _coerce_role({"id": "wf", "kind": "sensor", "match": "water(filter"})


def test_entity_satisfies_role_match_filter() -> None:
    """A ``match`` role narrows to entities whose entity_id OR friendly
    name matches, so ``water[ _]filter$`` picks the LG water-filter status
    sensor but not the months-in-use or fresh-air-filter siblings."""
    reg = _fake_registry({})  # match doesn't need the registry
    role = RoleSpec(id="wf", kind="sensor", match=r"water[ _]filter$")

    def s(entity_id, name):
        return SimpleNamespace(entity_id=entity_id, attributes={"friendly_name": name})

    status = s("sensor.refrigerator_water_filter", "Water filter")
    used = s("sensor.refrigerator_water_filter_used", "Water filter used")
    fresh = s("sensor.refrigerator_fresh_air_filter", "Fresh air filter")
    assert _entity_satisfies_role(role, status, reg) is True
    assert _entity_satisfies_role(role, used, reg) is False
    assert _entity_satisfies_role(role, fresh, reg) is False

    # Renamed entity_id but the friendly name still carries the label —
    # match on name keeps the role resolving.
    renamed = s("sensor.kitchen_thing_42", "Water filter")
    assert _entity_satisfies_role(role, renamed, reg) is True


async def test_resolver_fails_when_no_moisture_sensors(
    hass, leak_bundle_dir: Path
) -> None:
    # Seed lights + covers but no leak sensors.
    hass.states.async_set("cover.kitchen_valve", "open")
    hass.states.async_set(
        "light.alarm_strip",
        "off",
        {"supported_color_modes": ["rgb"]},
    )
    bundle = await async_load_bundle(hass, "leak-lockdown")
    # Provide the required selections so alarm_lights/lockdown_covers
    # don't fail for "user hasn't picked yet" — we want to verify
    # specifically that the (auto-selection) leak_sensors role is what
    # halts when the home is missing the devices.
    report = resolve(
        bundle.manifest,
        hass,
        selections={
            "alarm_lights": ["light.alarm_strip"],
            "lockdown_covers": ["cover.kitchen_valve"],
        },
    )
    assert not report.ok
    failures = report.failures()
    assert len(failures) == 1
    assert failures[0].role.id == "leak_sensors"
    assert "found none" in failures[0].reason


async def test_required_role_without_selection_fails_with_pick_message(
    hass, leak_bundle_dir: Path
) -> None:
    """``selection: required`` roles surface candidates but stay
    ``ok: False`` until the wizard picks. The failure reason should
    point at the act-on action (pick) rather than the home (install
    more devices) since the candidates already exist.
    """
    _seed_leak_home(hass)
    bundle = await async_load_bundle(hass, "leak-lockdown")
    # No selections passed → alarm_lights (required, min_count=1)
    # falls short.
    report = resolve(bundle.manifest, hass)
    assert not report.ok
    failed_ids = {f.role.id for f in report.failures()}
    assert "alarm_lights" in failed_ids
    alarm_fail = next(f for f in report.failures() if f.role.id == "alarm_lights")
    assert "select at least" in alarm_fail.reason
    # The candidates are still surfaced so the wizard can render them.
    assert len(alarm_fail.candidates) >= 1


async def test_required_role_filters_out_non_candidate_selections(
    hass, leak_bundle_dir: Path
) -> None:
    """The wizard could be stale (entity renamed between preview and
    install). The resolver must silently drop selections that no
    longer match the role's filter rather than passing them through
    to the renderer.
    """
    _seed_leak_home(hass)
    bundle = await async_load_bundle(hass, "leak-lockdown")
    report = resolve(
        bundle.manifest,
        hass,
        selections={
            "lockdown_covers": ["cover.kitchen_valve", "cover.does_not_exist"],
            "alarm_lights": [
                "light.alarm_strip_one",
                # Wrong domain — must be filtered out.
                "binary_sensor.kitchen_leak",
            ],
        },
    )
    # Bogus picks dropped; valid ones kept.
    assert report.bindings["lockdown_covers"] == ["cover.kitchen_valve"]
    assert report.bindings["alarm_lights"] == ["light.alarm_strip_one"]


async def test_resolver_caps_max_count(hass, leak_bundle_dir: Path) -> None:
    """alarm_lights max_count is 3. With 5 candidates plus a user
    selection covering all of them, the resolver must clip the
    selection to the first 3 alphabetical entries — protects the
    renderer from a user accidentally over-picking."""
    _seed_leak_home(hass)
    for n in range(3, 6):
        hass.states.async_set(
            f"light.alarm_strip_{n}",
            "off",
            {"supported_color_modes": ["rgb"]},
        )
    bundle = await async_load_bundle(hass, "leak-lockdown")
    every_light = [
        "light.alarm_strip_one",
        "light.alarm_strip_two",
        "light.alarm_strip_3",
        "light.alarm_strip_4",
        "light.alarm_strip_5",
    ]
    report = resolve(
        bundle.manifest,
        hass,
        selections={
            "alarm_lights": every_light,
            "lockdown_covers": ["cover.kitchen_valve"],
        },
    )
    assert report.ok, report.failures()
    bound = report.bindings["alarm_lights"]
    # Capped at max_count (3) — the first 3 in the user's selection
    # order. Required-selection roles take the user's order rather
    # than re-sorting alphabetically; the wizard exposes candidates in
    # alphabetical order so the user CAN pick alphabetically if they
    # want, but doesn't have to.
    assert len(bound) == 3
    assert bound == every_light[:3]
    # All 5 are still exposed as candidates so the wizard can render
    # them as toggles — only the selection got clipped.
    assert len(report.candidates["alarm_lights"]) == 5


# ── Inputs validator ────────────────────────────────────────────────


async def test_validate_inputs_coerces_and_defaults(
    hass, leak_bundle_dir: Path
) -> None:
    bundle = await async_load_bundle(hass, "leak-lockdown")
    report = validate_inputs(bundle.manifest, {"alarm_brightness": "75"})
    assert report.ok
    assert report.values == {
        "alarm_brightness": 75,  # coerced from string
        "all_clear_delay_minutes": 5,  # default
    }


async def test_validate_inputs_flags_out_of_range(
    hass, leak_bundle_dir: Path
) -> None:
    bundle = await async_load_bundle(hass, "leak-lockdown")
    report = validate_inputs(bundle.manifest, {"alarm_brightness": 250})
    assert not report.ok
    assert any(
        i.input_id == "alarm_brightness" and "above maximum" in i.reason
        for i in report.issues
    )


async def test_validate_inputs_select_rejects_bad_choice(
    hass, bedtime_bundle_dir: Path
) -> None:
    bundle = await async_load_bundle(hass, "bedtime-routine")
    report = validate_inputs(
        bundle.manifest, {"greeting_style": "obnoxious"}
    )
    assert not report.ok
    assert any(
        i.input_id == "greeting_style" for i in report.issues
    )


# ── Renderer ────────────────────────────────────────────────────────


_LEAK_FULL_SELECTION = {
    "lockdown_covers": ["cover.kitchen_valve"],
    "alarm_lights": ["light.alarm_strip_one", "light.alarm_strip_two"],
}
_BEDTIME_FULL_SELECTION = {
    "bedroom_lights": ["light.bedroom", "light.hallway"],
    "door_locks": ["lock.front_door"],
    "thermostat": ["climate.upstairs"],
}


async def test_render_leak_lockdown(hass, leak_bundle_dir: Path) -> None:
    _seed_leak_home(hass)
    bundle = await async_load_bundle(hass, "leak-lockdown")
    resolution = resolve(
        bundle.manifest, hass, selections=_LEAK_FULL_SELECTION
    )
    input_report = validate_inputs(bundle.manifest, {})
    rendered = render_package(
        bundle=bundle,
        resolution=resolution,
        inputs=input_report.values or {},
    )
    # Contents must parse as YAML and have both automations.
    parsed = yaml.safe_load(rendered.yaml_text)
    assert "automation" in parsed
    auto_aliases = {a["alias"] for a in parsed["automation"]}
    assert auto_aliases == {
        "Leak Lockdown — engage",
        "Leak Lockdown — all clear",
    }
    # The engage automation triggers on ALL leak sensors.
    engage = next(
        a for a in parsed["automation"] if a["alias"].endswith("engage")
    )
    assert set(engage["trigger"][0]["entity_id"]) == {
        "binary_sensor.kitchen_leak",
        "binary_sensor.basement_leak",
    }
    # The all-clear automation has the AND-state condition.
    all_clear = next(
        a for a in parsed["automation"] if a["alias"].endswith("all clear")
    )
    assert any(
        c.get("condition") == "state"
        and c.get("state") == "off"
        and set(c["entity_id"]) == {
            "binary_sensor.kitchen_leak",
            "binary_sensor.basement_leak",
        }
        for c in (all_clear.get("condition") or [])
    )
    # Header comment present so field techs know it's generated.
    assert "Generated by Selora AI" in rendered.yaml_text
    assert "DO NOT EDIT BY HAND" in rendered.yaml_text


async def test_render_bedtime_routine(
    hass, bedtime_bundle_dir: Path
) -> None:
    _seed_bedtime_home(hass)
    bundle = await async_load_bundle(hass, "bedtime-routine")
    resolution = resolve(
        bundle.manifest, hass, selections=_BEDTIME_FULL_SELECTION
    )
    input_report = validate_inputs(bundle.manifest, {})
    rendered = render_package(
        bundle=bundle,
        resolution=resolution,
        inputs=input_report.values or {},
    )
    parsed = yaml.safe_load(rendered.yaml_text)
    auto = parsed["automation"][0]
    assert auto["trigger"][0]["at"] == "22:30"
    # Optional lock + thermostat both wired up by the conditional blocks.
    services = [step.get("service") for step in auto["action"]]
    assert "lock.lock" in services
    assert "climate.set_temperature" in services
    assert "notify.notify" in services  # announce_on_finish default = true


async def test_tornado_alert_auto_resolver_hides_station_code(
    hass, tornado_bundle_dir: Path
) -> None:
    """The ``station_code`` input is flagged ``resolver:`` so it must
    NOT appear in the wizard's Settings payload. The homeowner never
    types it; the pipeline computes it from lat/lon at preview time.
    """
    from custom_components.selora_ai.recipes.pipeline_items import derive_items

    bundle = await async_load_bundle(hass, "tornado-alert")
    station_input = next(
        (i for i in bundle.manifest.inputs if i.id == "station_code"),
        None,
    )
    assert station_input is not None
    assert station_input.resolver == "nws_station_from_location"

    # Drive the wizard items derivation as the WS handler would.
    _seed_tornado_home(hass)
    resolution = resolve(
        bundle.manifest, hass, selections=_TORNADO_FULL_SELECTION
    )
    # Build a minimal PipelineResult so derive_items has something to
    # walk; the inputs row should still be present but only carry
    # the non-auto-resolved fields.
    from custom_components.selora_ai.recipes.pipeline import PipelineResult

    fake_result = PipelineResult(
        ok=True,
        stage_reached="render",
        bindings=resolution.bindings,
        candidates=resolution.candidates,
        pinned=resolution.pinned,
        selection_modes={r.id: r.selection for r in bundle.manifest.roles},
    )
    items = derive_items(
        bundle.manifest,
        fake_result,
        integrations_loaded=set(),
    )
    inputs_items = [it for it in items if it.kind == "inputs"]
    assert len(inputs_items) == 1
    payload_ids = [i["id"] for i in inputs_items[0].payload["inputs"]]
    assert "station_code" not in payload_ids
    assert {"shelter_zone", "warning_message"}.issubset(set(payload_ids))


async def test_render_tornado_alert(
    hass, tornado_bundle_dir: Path
) -> None:
    """The tornado-alert recipe exercises the inline-integration path
    (its manifest lists ``nws``) and templates the weather entity id
    from the homeowner-provided METAR code.
    """
    _seed_tornado_home(hass)
    bundle = await async_load_bundle(hass, "tornado-alert")
    resolution = resolve(
        bundle.manifest, hass, selections=_TORNADO_FULL_SELECTION
    )
    input_report = validate_inputs(
        bundle.manifest,
        {
            "station_code": "KOKC",
            "shelter_zone": "the basement",
            "warning_message": "urgent",
        },
    )
    rendered = render_package(
        bundle=bundle,
        resolution=resolution,
        inputs=input_report.values or {},
    )
    parsed = yaml.safe_load(rendered.yaml_text)
    # Two automations: watch + warning.
    aliases = {a["alias"] for a in parsed["automation"]}
    assert aliases == {
        "Tornado Alert — warning response",
        "Tornado Alert — watch heads-up",
    }
    # The METAR template flows into both triggers (lowercased).
    triggers = [a["trigger"][0]["entity_id"] for a in parsed["automation"]]
    assert all(t == "weather.kokc" for t in triggers)
    # Manifest declares the NWS integration prereq.
    assert any(i.domain == "nws" for i in bundle.manifest.integrations)


# ── configuration.yaml include ──────────────────────────────────────


def test_slug_to_filename_yields_valid_ha_package_name() -> None:
    # HA package names must be lowercase slugs ([a-z0-9_]). The manifest loader
    # accepts uppercase + hyphens, so the filename conversion must normalise
    # both or the package is rejected on reload after it's written.
    assert _slug_to_filename("leak-lockdown") == "leak_lockdown"
    assert _slug_to_filename("My-Recipe") == "my_recipe"
    assert _slug_to_filename("Baby_Sleep") == "baby_sleep"
    import re

    for slug in ("leak-lockdown", "My-Recipe", "Baby_Sleep", "ABC123"):
        assert re.fullmatch(r"[a-z0-9_]+", _slug_to_filename(slug))


@pytest.mark.parametrize(
    "payload",
    [
        "x: {{ cycler.__init__.__globals__ }}",
        "x: {{ self.__init__.__globals__ }}",
        "x: {{ ''.__class__.__mro__[1].__subclasses__() }}",
        "x: {{ request.__class__ }}",
    ],
)
def test_renderer_sandbox_blocks_python_introspection(payload: str) -> None:
    """Recipe templates are attacker-controllable (public catalog / pasted
    URL / upload). The render environment MUST be sandboxed so a template
    can't reach Python internals — a plain jinja2.Environment would make
    this remote code execution at render (preview) time. The sandbox turns
    the gadget access into a SecurityError, surfaced as RenderError."""
    env = _build_environment({"t.yaml.j2": payload})
    with pytest.raises(RenderError):
        _render_one(env, "t.yaml.j2", {})


def test_configuration_yaml_packages_include_added() -> None:
    original = "default_config:\n"
    updated = _ensure_packages_include(original)
    assert _has_packages_include(updated)
    assert "default_config:" in updated  # original content preserved
    # Idempotent: a second pass over an already-included file is a no-op.
    assert _ensure_packages_include(updated) == updated


def test_configuration_yaml_packages_include_under_existing_block() -> None:
    original = "homeassistant:\n  name: Home\n"
    updated = _ensure_packages_include(original)
    assert "name: Home" in updated
    assert "packages: !include_dir_named packages" in updated


def test_ensure_packages_include_refuses_unsafe_homeassistant_form() -> None:
    # An inline homeassistant form that isn't a bare block header must NOT
    # get a second prepended ``homeassistant:`` block (duplicate key →
    # broken reload). Surface it instead.
    original = "homeassistant: !include_dir_merge_named ha_config/\n"
    with pytest.raises(PackagerError):
        _ensure_packages_include(original)


def test_packages_include_routes_into_included_homeassistant_file(
    hass, tmp_path
) -> None:
    # configuration.yaml uses ``homeassistant: !include homeassistant.yaml``.
    # The packages key belongs in the included file, and configuration.yaml
    # must be left untouched (no duplicate top-level homeassistant: block).
    hass.config.config_dir = str(tmp_path)
    config_path = tmp_path / "configuration.yaml"
    config_text = "homeassistant: !include homeassistant.yaml\ndefault_config:\n"
    config_path.write_text(config_text, encoding="utf-8")
    included = tmp_path / "homeassistant.yaml"
    included.write_text("name: Home\n", encoding="utf-8")

    changed = ensure_packages_include(hass)
    assert changed is True
    # configuration.yaml left untouched → no duplicate homeassistant: block.
    assert config_path.read_text(encoding="utf-8") == config_text
    # Packages include landed in the included file.
    assert _has_packages_include(included.read_text(encoding="utf-8"))
    # Idempotent: a second pass makes no further change.
    assert ensure_packages_include(hass) is False


@pytest.mark.parametrize("quote", ['"', "'"])
def test_packages_include_routes_into_quoted_included_file(
    hass, tmp_path, quote
) -> None:
    # YAML allows a quoted include target (`!include "homeassistant.yaml"`).
    # The quotes must be stripped so we edit the real file — not create one
    # literally named `"homeassistant.yaml"`, which would leave packages
    # unloaded while the install reports success.
    hass.config.config_dir = str(tmp_path)
    config_path = tmp_path / "configuration.yaml"
    config_text = f"homeassistant: !include {quote}homeassistant.yaml{quote}\n"
    config_path.write_text(config_text, encoding="utf-8")
    included = tmp_path / "homeassistant.yaml"
    included.write_text("name: Home\n", encoding="utf-8")

    assert ensure_packages_include(hass) is True
    # The real included file got the include — not a quoted-name decoy.
    assert _has_packages_include(included.read_text(encoding="utf-8"))
    assert not (tmp_path / f"{quote}homeassistant.yaml{quote}").exists()
    assert config_path.read_text(encoding="utf-8") == config_text


# ── Pipeline end-to-end ─────────────────────────────────────────────


async def test_preview_renders_without_writing(
    hass, leak_bundle_dir: Path
) -> None:
    _seed_leak_home(hass)
    result = await async_preview(
        hass,
        slug="leak-lockdown",
        inputs={},
        selections=_LEAK_FULL_SELECTION,
    )
    assert result.ok, result.punch_list
    assert result.stage_reached == "render"
    assert result.preview is not None
    assert "Leak Lockdown" in result.preview.yaml_text
    # Disk untouched.
    assert not package_path(hass, "leak-lockdown").exists()


async def test_install_writes_package_and_record(
    hass, leak_bundle_dir: Path
) -> None:
    _seed_leak_home(hass)
    result = await async_install(
        hass,
        slug="leak-lockdown",
        inputs={"alarm_brightness": 80, "all_clear_delay_minutes": 3},
        selections=_LEAK_FULL_SELECTION,
    )
    assert result.ok, result.punch_list
    assert result.stage_reached == "complete"
    assert result.record is not None
    # File landed.
    target = package_path(hass, "leak-lockdown")
    assert target.is_file()
    text = target.read_text(encoding="utf-8")
    assert "Leak Lockdown — engage" in text
    assert "brightness_pct: 80" in text
    # configuration.yaml now has the packages include.
    config_text = (
        Path(hass.config.config_dir) / "configuration.yaml"
    ).read_text(encoding="utf-8")
    assert _has_packages_include(config_text)


async def test_install_surfaces_reload_core_config_failure(
    hass, leak_bundle_dir: Path
) -> None:
    """A failing ``homeassistant.reload_core_config`` must abort the install
    as ``reload_failed`` — not silently record the recipe as active when HA
    never loaded the package.
    """
    from custom_components.selora_ai.recipes.store import get_install_store

    _seed_leak_home(hass)

    async def _boom(_call) -> None:
        raise RuntimeError("invalid configuration.yaml")

    hass.services.async_register("homeassistant", "reload_core_config", _boom)

    result = await async_install(
        hass, slug="leak-lockdown", selections=_LEAK_FULL_SELECTION
    )
    assert not result.ok
    assert result.stage_reached == "reload"
    assert any(item.code == "reload_failed" for item in result.punch_list)
    # The recipe must NOT be recorded as installed.
    assert await get_install_store(hass).async_get("leak-lockdown") is None


async def test_ws_package_returns_yaml_and_counts(
    hass, leak_bundle_dir: Path
) -> None:
    """The package WS reads the installed file and summarises its sections so
    the panel can show what a recipe created + let the user view the YAML.
    """
    from unittest.mock import MagicMock

    from custom_components.selora_ai.recipes.ws import _ws_recipes_package

    _seed_leak_home(hass)
    install = await async_install(
        hass, slug="leak-lockdown", selections=_LEAK_FULL_SELECTION
    )
    assert install.ok, install.punch_list

    connection = MagicMock()
    connection.user.is_admin = True
    await _ws_recipes_package.__wrapped__(
        hass,
        connection,
        {"id": 1, "type": "selora_ai/recipes/package", "slug": "leak-lockdown"},
    )
    connection.send_error.assert_not_called()
    connection.send_result.assert_called_once()
    payload = connection.send_result.call_args[0][1]
    assert "Leak Lockdown" in payload["yaml"]
    assert payload["counts"].get("automation", 0) >= 1
    assert payload["package_path"].endswith(".yaml")


async def test_ws_package_missing_file_errors(hass, tmp_path) -> None:
    from unittest.mock import MagicMock

    from custom_components.selora_ai.recipes.ws import _ws_recipes_package

    hass.config.config_dir = str(tmp_path)
    connection = MagicMock()
    connection.user.is_admin = True
    await _ws_recipes_package.__wrapped__(
        hass,
        connection,
        {"id": 1, "type": "selora_ai/recipes/package", "slug": "never-installed"},
    )
    connection.send_result.assert_not_called()
    connection.send_error.assert_called_once()
    assert connection.send_error.call_args[0][1] == "not_found"


async def test_install_punch_list_when_role_unmet(
    hass, leak_bundle_dir: Path
) -> None:
    # No moisture sensors seeded.
    hass.states.async_set(
        "light.something",
        "off",
        {"supported_color_modes": ["rgb"]},
    )
    result = await async_install(hass, slug="leak-lockdown")
    assert not result.ok
    assert result.stage_reached == "resolve"
    targets = {item.target for item in result.punch_list}
    assert "leak_sensors" in targets


async def test_uninstall_removes_package_and_record(
    hass, leak_bundle_dir: Path
) -> None:
    _seed_leak_home(hass)
    install_result = await async_install(
        hass, slug="leak-lockdown", selections=_LEAK_FULL_SELECTION
    )
    assert install_result.ok, install_result.punch_list
    assert package_path(hass, "leak-lockdown").exists()
    assert leak_bundle_dir.is_dir()
    uninstall_result = await async_uninstall(hass, "leak-lockdown")
    assert uninstall_result.ok
    assert not package_path(hass, "leak-lockdown").exists()
    # Staged bundle dir removed too → drops out of "On this device".
    assert not leak_bundle_dir.exists()
    bundles = await async_list_bundles(hass)
    assert "leak-lockdown" not in {b.manifest.slug for b in bundles}
    # Record gone too.
    from custom_components.selora_ai.recipes.store import get_install_store

    assert await get_install_store(hass).async_get("leak-lockdown") is None


async def test_uninstall_unknown_slug_is_idempotent(hass, tmp_path) -> None:
    hass.config.config_dir = str(tmp_path)
    result = await async_uninstall(hass, "never-installed")
    assert result.ok  # nothing to do, but no failure


async def test_list_bundles_finds_demo_recipes(
    hass, leak_bundle_dir: Path, bedtime_bundle_dir: Path
) -> None:
    bundles = await async_list_bundles(hass)
    slugs = {b.manifest.slug for b in bundles}
    assert {"leak-lockdown", "bedtime-routine"} <= slugs


# ── Manifest pins / deferred bindings ──────────────────────────────


def _write_pinned_recipe(tmp_path: Path) -> Path:
    """Build a tiny bundle with a ``bindings:`` block, two pins (one
    that should resolve, one that should stay pending), and a Jinja
    template that emits one automation per bound light. Returns the
    bundle root.
    """
    root = tmp_path / "pinned-demo"
    (root / "package" / "automations").mkdir(parents=True)
    (root / "manifest.yaml").write_text(
        "slug: pinned-demo\n"
        "version: 1.0.0\n"
        "title: Pinned Demo\n"
        "roles:\n"
        "  - id: hub_lights\n"
        "    kind: light\n"
        "    min_count: 1\n"
        "    selection: required\n"
        "bindings:\n"
        "  hub_lights:\n"
        "    - entity_id: light.present_one\n"
        "      identity:\n"
        "        manufacturer: Signify\n"
        "        model: Hue A19\n"
        "        integration: hue\n"
        "      note: pre-existing\n"
        "    - entity_id: light.not_paired_yet\n"
        "      identity:\n"
        "        manufacturer: Signify\n"
        "        model: Hue Lightstrip\n"
        "package_files:\n"
        "  - package/automations/a.yaml.j2\n",
        encoding="utf-8",
    )
    (root / "package" / "automations" / "a.yaml.j2").write_text(
        "automation:\n"
        "  - id: pinned_demo\n"
        "    alias: Pinned Demo\n"
        "    trigger:\n"
        "      - platform: state\n"
        "        entity_id:\n"
        "{% for e in roles.hub_lights %}"
        "          - {{ e }}\n"
        "{% endfor %}"
        "    action: []\n",
        encoding="utf-8",
    )
    return root


async def test_resolver_locks_present_pin_and_lists_pending_one(
    hass, tmp_path: Path
) -> None:
    """The resolved binding is ``pinned``; the missing one shows up
    in ``pending`` with full identity attached. The role's ``selected``
    list contains only the resolved pin.
    """
    hass.config.config_dir = str(tmp_path)
    bundle_root = _write_pinned_recipe(tmp_path)
    # Stage into the bundles dir so async_load_bundle finds it.
    dest = Path(hass.config.config_dir) / RECIPE_BUNDLE_DIR / "pinned-demo"
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(bundle_root, dest)

    hass.states.async_set("light.present_one", "off")
    # light.not_paired_yet deliberately absent.

    bundle = await async_load_bundle(hass, "pinned-demo")
    report = resolve(bundle.manifest, hass)

    role = report.roles[0]
    assert role.role.id == "hub_lights"
    pinned_ids = [p.entity_id for p in role.pinned]
    assert pinned_ids == ["light.present_one"]
    pending_ids = [p.binding.entity_id for p in role.pending]
    assert pending_ids == ["light.not_paired_yet"]
    # The pending pin's identity is preserved.
    pending = role.pending[0].binding
    assert pending.manufacturer == "Signify"
    assert pending.model == "Hue Lightstrip"
    # ``selected`` only contains resolved pins until the missing one
    # arrives (or the user picks something else).
    assert role.selected == ("light.present_one",)
    # Role is NOT ok: pin is still pending.
    assert role.ok is False
    assert "waiting on" in role.reason


async def test_pipeline_surfaces_binding_pending_in_punch_list(
    hass, tmp_path: Path
) -> None:
    """The pipeline turns each pending binding into a separate
    ``binding_pending`` punch item carrying device identity hints so
    the wizard can render a device card.
    """
    hass.config.config_dir = str(tmp_path)
    bundle_root = _write_pinned_recipe(tmp_path)
    dest = Path(hass.config.config_dir) / RECIPE_BUNDLE_DIR / "pinned-demo"
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(bundle_root, dest)

    hass.states.async_set("light.present_one", "off")
    result = await async_preview(hass, slug="pinned-demo")

    assert not result.ok
    assert result.stage_reached == "resolve"
    pending_items = [p for p in result.punch_list if p.code == "binding_pending"]
    assert len(pending_items) == 1
    item = pending_items[0]
    assert item.target == "hub_lights"
    assert item.identity["entity_id"] == "light.not_paired_yet"
    assert item.identity["model"] == "Hue Lightstrip"
    # Pinned aggregate is also surfaced for the locked-chip render.
    assert result.pinned == {"hub_lights": ["light.present_one"]}


async def test_resolved_pin_unblocks_install(
    hass, tmp_path: Path
) -> None:
    """Once every pin is present (the device got paired), the role
    flips to ok and the install button becomes reachable.
    """
    hass.config.config_dir = str(tmp_path)
    bundle_root = _write_pinned_recipe(tmp_path)
    dest = Path(hass.config.config_dir) / RECIPE_BUNDLE_DIR / "pinned-demo"
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(bundle_root, dest)

    # Both pins now resolve.
    hass.states.async_set("light.present_one", "off")
    hass.states.async_set("light.not_paired_yet", "off")

    bundle = await async_load_bundle(hass, "pinned-demo")
    report = resolve(bundle.manifest, hass)
    role = report.roles[0]
    assert role.ok
    assert set(role.selected) == {"light.present_one", "light.not_paired_yet"}
    # No pending entries left.
    assert role.pending == ()


# ── v3 prototype: binding-via-group ─────────────────────────────────


async def test_v3_render_emits_group_block_and_group_refs(
    hass, v3_bedtime_bundle_dir: Path
) -> None:
    """v3's renderer must (1) emit a ``group:`` section with one
    entry per role + its bound entity list, and (2) reference each
    group via ``group.selora_<slug>_<role>`` in the automation
    targets (NOT literal entity ids)."""
    _seed_v3_bedtime_home(hass)
    result = await async_install(
        hass,
        slug='bedtime-routine-v3',
        inputs={},
        selections=_V3_BEDTIME_FULL_SELECTION,
    )
    assert result.ok, result.punch_list
    parsed = yaml.safe_load(result.preview.yaml_text)
    # Group block created with the role members.
    assert 'group' in parsed
    assert parsed['group']['selora_bedtime_routine_v3_bedroom_lights'][
        'entities'
    ] == ['light.bedroom', 'light.hallway']
    # Automation references the group, not the literal entity ids.
    auto = parsed['automation'][0]
    actions = auto['action']
    light_action = next(
        a for a in actions if a.get('service') == 'light.turn_off'
    )
    assert (
        light_action['target']['entity_id']
        == 'group.selora_bedtime_routine_v3_bedroom_lights'
    )
    assert 'light.bedroom' not in str(light_action)


async def test_v3_rebind_updates_group_without_rerender(
    hass, v3_bedtime_bundle_dir: Path
) -> None:
    """Core pass criterion: device replacement without re-install.
    Install with one set of lights, rebind to a different set, the
    package YAML's automation block must be byte-identical and only
    the group entities must change.
    """
    _seed_v3_bedtime_home(hass)
    install_result = await async_install(
        hass,
        slug='bedtime-routine-v3',
        inputs={},
        selections=_V3_BEDTIME_FULL_SELECTION,
    )
    assert install_result.ok
    path = package_path(hass, 'bedtime-routine-v3')
    yaml_before = path.read_text(encoding='utf-8')
    parsed_before = yaml.safe_load(yaml_before)
    automation_before = parsed_before['automation']

    # Swap in a different light — same role, different entity.
    from custom_components.selora_ai.recipes.packager import (
        update_package_groups,
    )
    update_package_groups(
        hass,
        'bedtime-routine-v3',
        {
            'selora_bedtime_routine_v3_bedroom_lights': [
                'light.spare',
                'light.hallway',
            ],
            'selora_bedtime_routine_v3_door_locks': ['lock.front_door'],
            'selora_bedtime_routine_v3_thermostat': ['climate.upstairs'],
        },
    )
    parsed_after = yaml.safe_load(path.read_text(encoding='utf-8'))
    # Automations untouched.
    assert parsed_after['automation'] == automation_before
    # Group membership swapped.
    assert parsed_after['group'][
        'selora_bedtime_routine_v3_bedroom_lights'
    ]['entities'] == ['light.spare', 'light.hallway']


async def test_v3_rebind_preserves_install_metadata(
    hass, v3_bedtime_bundle_dir: Path
) -> None:
    """Rebinding through the WS handler must not wipe the record's
    ``integrations_installed`` / ``dashboard_card``. async_record() does
    a full overwrite, so the handler has to carry them forward — losing
    them breaks uninstall's owned-entry removal and dashboard cleanup.
    """
    from unittest.mock import MagicMock

    from custom_components.selora_ai.recipes.store import get_install_store
    from custom_components.selora_ai.recipes.ws import _ws_recipes_rebind

    _seed_v3_bedtime_home(hass)
    install_result = await async_install(
        hass,
        slug='bedtime-routine-v3',
        inputs={},
        selections=_V3_BEDTIME_FULL_SELECTION,
    )
    assert install_result.ok

    # Simulate a recipe that auto-created a config entry and placed a
    # dashboard card — the metadata uninstall later relies on.
    store = get_install_store(hass)
    base = await store.async_get('bedtime-routine-v3')
    await store.async_record(
        slug=base.slug,
        version=base.version,
        title=base.title,
        package_path=base.package_path,
        bindings=base.bindings,
        inputs=base.inputs,
        integrations_installed={'nws': 'entry_abc123'},
        dashboard_card={'ok': True, 'target': 'lovelace', 'view': 0},
    )

    connection = MagicMock()
    connection.user.is_admin = True
    msg = {
        'id': 1,
        'type': 'selora_ai/recipes/rebind',
        'slug': 'bedtime-routine-v3',
        'selections': {
            'bedroom_lights': ['light.spare', 'light.hallway'],
            'door_locks': ['lock.front_door'],
            'thermostat': ['climate.upstairs'],
        },
    }
    # The registered handler is wrapped by async_response/websocket_command
    # into a sync scheduler; ``__wrapped__`` is the underlying coroutine.
    await _ws_recipes_rebind.__wrapped__(hass, connection, msg)

    connection.send_error.assert_not_called()
    connection.send_result.assert_called_once()

    record = await store.async_get('bedtime-routine-v3')
    # Metadata survived the rebind.
    assert record.integrations_installed == {'nws': 'entry_abc123'}
    assert record.dashboard_card == {
        'ok': True,
        'target': 'lovelace',
        'view': 0,
    }
    # Bindings reflect the new selection.
    assert record.bindings['bedroom_lights'] == ['light.spare', 'light.hallway']


async def test_v3_uninstall_removes_package_and_groups(
    hass, v3_bedtime_bundle_dir: Path
) -> None:
    """Atomic uninstall: removing the package file is the only step;
    every group declared in the package goes with it because they're
    in the same file."""
    _seed_v3_bedtime_home(hass)
    install_result = await async_install(
        hass,
        slug='bedtime-routine-v3',
        inputs={},
        selections=_V3_BEDTIME_FULL_SELECTION,
    )
    assert install_result.ok
    path = package_path(hass, 'bedtime-routine-v3')
    assert path.exists()
    uninstall_result = await async_uninstall(hass, 'bedtime-routine-v3')
    assert uninstall_result.ok
    assert not path.exists()


async def test_v3_and_literal_modes_coexist(
    hass, v3_bedtime_bundle_dir: Path
) -> None:
    """Installing the v3 demo and the classic literal-mode bedtime
    at the same time must not interfere — each produces its own
    package file with its own structure."""
    _seed_v3_bedtime_home(hass)
    # The literal-mode bedtime fixture seeds different state, so we
    # also need bed_light/bedroom_lamp for the pinned bindings.
    hass.states.async_set('light.bed_light', 'off')
    hass.states.async_set('light.bedroom_lamp', 'off')

    v3_result = await async_install(
        hass,
        slug='bedtime-routine-v3',
        inputs={},
        selections=_V3_BEDTIME_FULL_SELECTION,
    )
    classic_result = await async_install(
        hass,
        slug='bedtime-routine',
        inputs={},
        selections={
            'bedroom_lights': ['light.bedroom', 'light.hallway'],
            'door_locks': ['lock.front_door'],
            'thermostat': ['climate.upstairs'],
        },
    )
    assert v3_result.ok and classic_result.ok
    v3_yaml = package_path(hass, 'bedtime-routine-v3').read_text()
    classic_yaml = package_path(hass, 'bedtime-routine').read_text()
    # v3 has a group: block; literal mode does not.
    assert 'group:' in v3_yaml
    assert 'group:' not in classic_yaml
    # Different files; neither references the other's automations.
    assert 'bedtime_routine_v3' in v3_yaml
    assert 'bedtime_routine_v3' not in classic_yaml

