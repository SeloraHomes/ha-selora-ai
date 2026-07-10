"""Tests for the recipe minimum-integration-version gate.

A recipe manifest may declare ``min_integration_version``; recipes that
require a newer integration than the one installed are hidden from the
catalog. Covers the pure comparison helper, manifest parsing, and the
catalog websocket handler's filtering.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.core import HomeAssistant
import pytest

from custom_components.selora_ai.recipes.manifest import ManifestError, load_manifest
from custom_components.selora_ai.recipes.version_gate import (
    _release_tuple,
    integration_version,
    meets_minimum,
)
from custom_components.selora_ai.recipes.ws import _ws_recipes_catalog

# The undecorated coroutine behind @async_response + @websocket_command.
_catalog_handler = _ws_recipes_catalog.__wrapped__


# ── meets_minimum ────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("current", "minimum", "expected"),
    [
        # Equal → satisfied.
        ("0.12.0", "0.12.0", True),
        # Newer current → satisfied.
        ("0.12.1", "0.12.0", True),
        ("1.0.0", "0.12.0", True),
        ("0.13.0", "0.12.0", True),
        # Older current → NOT satisfied (the whole point of the gate).
        ("0.11.0", "0.12.0", False),
        ("0.9.9", "0.12.0", False),
        # Blank / absent minimum → no requirement → shown.
        ("0.11.0", "", True),
        ("0.11.0", "   ", True),
        # Unparseable minimum → can't enforce → shown.
        ("0.11.0", "garbage", True),
        # Unparseable / blank current → never blank the catalog → shown.
        ("", "0.12.0", True),
        ("unknown", "0.12.0", True),
        # Prerelease/build suffixes ignored: a 0.12.0 beta has 0.12.0's
        # features, so it satisfies a 0.12.0 floor.
        ("0.12.0b3", "0.12.0", True),
        ("0.12.0-pre.4", "0.12.0", True),
        ("v0.12.0", "0.12.0", True),
        # But a 0.11.x prerelease is still below a 0.12.0 floor.
        ("0.11.0-pre.9", "0.12.0", False),
        # Uneven component counts are zero-padded, not misordered.
        ("1.0", "1.0.0", True),
        ("1.0.1", "1.0", True),
        ("1.0", "1.0.1", False),
    ],
)
def test_meets_minimum(current: str, minimum: str, expected: bool) -> None:
    assert meets_minimum(current, minimum) is expected


def test_release_tuple_parses_and_rejects() -> None:
    assert _release_tuple("0.12.0") == (0, 12, 0)
    assert _release_tuple("v0.12.0b3") == (0, 12, 0)
    assert _release_tuple("1") == (1,)
    assert _release_tuple("") is None
    assert _release_tuple("garbage") is None


def test_integration_version_reads_real_manifest() -> None:
    """The installed integration reports a parseable version."""
    integration_version.cache_clear()
    version = integration_version()
    assert _release_tuple(version) is not None


# ── manifest parsing ─────────────────────────────────────────────────

_MANIFEST = """\
slug: demo
version: 1.0.0
title: Demo
{extra}package_files:
  - package/x.yaml.j2
"""


def _write_bundle(tmp_path: Path, extra: str) -> Path:
    bundle = tmp_path / "b"
    bundle.mkdir()
    (bundle / "package").mkdir()
    (bundle / "package" / "x.yaml.j2").write_text("automation: []\n")
    (bundle / "manifest.yaml").write_text(_MANIFEST.format(extra=extra))
    return bundle


def test_manifest_parses_min_integration_version(tmp_path: Path) -> None:
    manifest = load_manifest(_write_bundle(tmp_path, "min_integration_version: 0.12.0\n"))
    assert manifest.min_integration_version == "0.12.0"


def test_manifest_min_integration_version_defaults_blank(tmp_path: Path) -> None:
    manifest = load_manifest(_write_bundle(tmp_path, ""))
    assert manifest.min_integration_version == ""


def test_manifest_rejects_bad_min_integration_version(tmp_path: Path) -> None:
    with pytest.raises(ManifestError, match="min_integration_version"):
        load_manifest(_write_bundle(tmp_path, "min_integration_version: not-a-version\n"))


# ── catalog websocket filtering ──────────────────────────────────────


def _catalog(*entries: dict) -> dict:
    return {"generated_at": "2026-07-10T00:00:00Z", "count": len(entries), "recipes": list(entries)}


async def _run_catalog(hass: HomeAssistant, catalog: dict, current: str) -> dict:
    """Invoke the catalog handler with mocked catalog + version, return
    the payload passed to send_result.
    """
    conn = MagicMock()
    conn.user.is_admin = True
    store = MagicMock()
    store.async_list = AsyncMock(return_value=[])
    with (
        patch(
            "custom_components.selora_ai.recipes.ws.async_get_catalog",
            AsyncMock(return_value=catalog),
        ),
        patch(
            "custom_components.selora_ai.recipes.ws.integration_version",
            return_value=current,
        ),
        patch(
            "custom_components.selora_ai.recipes.ws.get_install_store",
            return_value=store,
        ),
    ):
        await _catalog_handler(hass, conn, {"id": 1, "type": "selora_ai/recipes/catalog"})
    conn.send_error.assert_not_called()
    return conn.send_result.call_args[0][1]


async def test_catalog_hides_incompatible_recipes(hass: HomeAssistant) -> None:
    catalog = _catalog(
        {"slug": "old-ok", "title": "Old OK"},  # no floor → always shown
        {"slug": "needs-new", "title": "Needs New", "min_integration_version": "0.12.0"},
        {"slug": "needs-old", "title": "Needs Old", "min_integration_version": "0.10.0"},
    )
    payload = await _run_catalog(hass, catalog, current="0.11.0")
    slugs = [r["slug"] for r in payload["recipes"]]
    assert slugs == ["old-ok", "needs-old"]
    assert payload["hidden_incompatible"] == 1
    assert payload["count"] == 2
    assert payload["integration_version"] == "0.11.0"


async def test_catalog_shows_all_when_up_to_date(hass: HomeAssistant) -> None:
    catalog = _catalog(
        {"slug": "a", "title": "A", "min_integration_version": "0.12.0"},
        {"slug": "b", "title": "B", "min_integration_version": "0.11.0"},
    )
    payload = await _run_catalog(hass, catalog, current="0.12.0")
    assert [r["slug"] for r in payload["recipes"]] == ["a", "b"]
    assert payload["hidden_incompatible"] == 0
