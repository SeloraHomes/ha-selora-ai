"""Tests for role-level ``integration`` scoping.

A recipe role can name an HA integration (``integration: lg_thinq``) so
the wizard only offers that integration's own entities — e.g. the
fridge's own doors, not every door/contact sensor in the home. Covers
parse + validation (manifest), candidate filtering (resolver), and the
friendly-title enrichment sent to the wizard (ws summary).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from homeassistant.helpers import entity_registry as er

from custom_components.selora_ai.recipes.manifest import (
    ManifestError,
    RoleSpec,
    load_manifest,
)
from custom_components.selora_ai.recipes.resolver import resolve
from custom_components.selora_ai.recipes.ws import _role_summary

_FRIDGE_MANIFEST = """\
slug: fridge
version: 1.0.0
title: Fridge
roles:
  - id: fridge_doors
    kind: binary_sensor
    device_class: door
    integration: lg_thinq
    min_count: 1
    selection: required
package_files:
  - package/x.yaml.j2
"""


def _write_bundle(tmp_path: Path, manifest_yaml: str) -> Path:
    bundle = tmp_path / "b"
    bundle.mkdir()
    (bundle / "package").mkdir()
    (bundle / "package" / "x.yaml.j2").write_text("automation: []\n")
    (bundle / "manifest.yaml").write_text(manifest_yaml)
    return bundle


def test_role_integration_parsed(tmp_path: Path) -> None:
    manifest = load_manifest(_write_bundle(tmp_path, _FRIDGE_MANIFEST))
    (role,) = manifest.roles
    assert role.integration == "lg_thinq"


def test_role_integration_rejects_bad_domain(tmp_path: Path) -> None:
    bad = _FRIDGE_MANIFEST.replace("integration: lg_thinq", 'integration: "lg thinq!"')
    with pytest.raises(ManifestError, match="integration"):
        load_manifest(_write_bundle(tmp_path, bad))


def test_role_integration_defaults_none(tmp_path: Path) -> None:
    plain = _FRIDGE_MANIFEST.replace("    integration: lg_thinq\n", "")
    manifest = load_manifest(_write_bundle(tmp_path, plain))
    assert manifest.roles[0].integration is None


async def test_role_integration_scopes_candidates(hass, tmp_path: Path) -> None:
    """Only entities created by the named integration are candidates —
    a same-device_class door from another integration is excluded."""
    reg = er.async_get(hass)
    e_lg = reg.async_get_or_create(
        "binary_sensor", "lg_thinq", "uid_fridge", suggested_object_id="fridge_door"
    )
    e_zha = reg.async_get_or_create(
        "binary_sensor", "zha", "uid_pantry", suggested_object_id="pantry_door"
    )
    hass.states.async_set(e_lg.entity_id, "off", {"device_class": "door"})
    hass.states.async_set(e_zha.entity_id, "off", {"device_class": "door"})

    manifest = load_manifest(_write_bundle(tmp_path, _FRIDGE_MANIFEST))
    report = resolve(
        manifest,
        hass,
        selections={"fridge_doors": [e_lg.entity_id, e_zha.entity_id]},
    )
    (role_res,) = report.roles
    assert role_res.candidates == (e_lg.entity_id,)
    # The zha door was offered by the user but isn't a candidate, so it
    # never binds.
    assert role_res.selected == (e_lg.entity_id,)
    assert report.ok, report.failures()


async def test_role_integration_excludes_registryless_entity(
    hass, tmp_path: Path
) -> None:
    """An entity with no registry entry can't be attributed to an
    integration, so a role that names one excludes it."""
    hass.states.async_set(
        "binary_sensor.floating_door", "off", {"device_class": "door"}
    )
    manifest = load_manifest(_write_bundle(tmp_path, _FRIDGE_MANIFEST))
    report = resolve(
        manifest,
        hass,
        selections={"fridge_doors": ["binary_sensor.floating_door"]},
    )
    (role_res,) = report.roles
    assert role_res.candidates == ()
    assert not role_res.ok


def test_role_summary_adds_friendly_integration_title() -> None:
    role = RoleSpec(id="fridge_doors", kind="binary_sensor", integration="lg_thinq")
    data = _role_summary(role)
    assert data["integration"] == "lg_thinq"
    assert data["integration_title"] == "LG ThinQ"


def test_role_summary_unknown_integration_falls_back_to_domain() -> None:
    role = RoleSpec(id="x", kind="binary_sensor", integration="made_up_domain")
    assert _role_summary(role)["integration_title"] == "made_up_domain"


def test_role_summary_no_integration_omits_title() -> None:
    role = RoleSpec(id="x", kind="light")
    assert "integration_title" not in _role_summary(role)


def test_integration_brands_resolves_titles_dedups_and_skips_empty() -> None:
    from custom_components.selora_ai.recipes.ws import _integration_brands

    brands = _integration_brands(
        [
            ("lg_thinq", ""),
            ("lg_thinq", "reads the fridge doors"),
            ("made_up_domain", ""),
            ("", "ignored"),
        ]
    )
    # Deduped by domain; the first non-empty reason for a domain wins;
    # empty domains are skipped; titles resolve from KNOWN_INTEGRATIONS.
    assert brands == [
        {"domain": "lg_thinq", "title": "LG ThinQ", "reason": "reads the fridge doors"},
        {"domain": "made_up_domain", "title": "made_up_domain", "reason": ""},
    ]
