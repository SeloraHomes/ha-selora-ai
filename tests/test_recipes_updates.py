"""Updating an installed recipe to a newer catalog version.

Covers the three pieces an update spans: the checker that decides a newer
version exists, the re-install that applies it with the record's own
choices, and the update entity that puts it in Settings → Updates.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import timedelta
from pathlib import Path
import shutil
import tarfile
from typing import Any
from unittest.mock import AsyncMock, patch

from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.selora_ai.const import (
    CONF_COLLECTOR_ENABLED,
    CONF_DISCOVERY_ENABLED,
    CONF_ENTRY_TYPE,
    CONF_INSIGHTS_ENABLED,
    DOMAIN,
    ENTRY_TYPE_LLM,
)
from custom_components.selora_ai.recipes.const import (
    RECIPE_BUNDLE_DIR,
    RECIPE_UPDATE_CHECK_INITIAL_DELAY_SECONDS,
    SIGNAL_RECIPE_UPDATES_CHANGED,
)
from custom_components.selora_ai.recipes.packager import package_path
from custom_components.selora_ai.recipes.pipeline import async_install
from custom_components.selora_ai.recipes.store import InstallRecord, get_install_store
from custom_components.selora_ai.recipes.updates import (
    async_update_recipe,
    get_update_checker,
    release_summary,
)
from custom_components.selora_ai.recipes.version_gate import is_newer

FIXTURES = Path(__file__).parent / "recipe_fixtures"
SLUG = "leak-lockdown"
CATALOG_URL = "https://selorahomes.com/api/recipes.json"
_SELECTION = {
    "lockdown_covers": ["cover.kitchen_valve"],
    "alarm_lights": ["light.alarm_strip_one", "light.alarm_strip_two"],
}
_CHANGELOG = """# Changelog

## v2.1.0 - 2026-10-06

- Flashes the alarm lights faster, so a leak is noticed from the next room.

## v2.0.0 - 2026-05-19

Initial release.
"""


def _write_bundle(
    dest: Path,
    *,
    version: str = "2.0.0",
    extra_integration: bool = False,
    slug: str = SLUG,
) -> Path:
    """Write the leak-lockdown fixture to ``dest`` as ``version``. With
    ``extra_integration`` the manifest also requires one the home lacks."""
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(FIXTURES / SLUG, dest)
    manifest = dest / "manifest.yaml"
    text = manifest.read_text(encoding="utf-8").replace("version: 2.0.0", f"version: {version}")
    text = text.replace(f"slug: {SLUG}", f"slug: {slug}")
    if extra_integration:
        text = text.replace(
            "integrations: []",
            "integrations:\n  - domain: wake_on_lan\n    title: Wake on LAN",
        )
    manifest.write_text(text, encoding="utf-8")
    return dest


def _stage(hass: HomeAssistant, **bundle: Any) -> Path:
    """Put a bundle where the loader finds it, as an install would."""
    slug = bundle.get("slug", SLUG)
    return _write_bundle(Path(hass.config.config_dir) / RECIPE_BUNDLE_DIR / slug, **bundle)


def _seed_home(hass: HomeAssistant) -> None:
    hass.states.async_set("binary_sensor.kitchen_leak", "off", {"device_class": "moisture"})
    hass.states.async_set("cover.kitchen_valve", "open")
    hass.states.async_set("light.alarm_strip_one", "off", {"supported_color_modes": ["rgb"]})
    hass.states.async_set("light.alarm_strip_two", "off", {"supported_color_modes": ["hs"]})


def _catalog(version: str, **extra: str) -> dict:
    return {
        "recipes": [
            {
                "slug": SLUG,
                "title": "Leak Lockdown",
                "version": version,
                "url": "https://selorahomes.com/recipes/safety/leak-lockdown/",
                "package_url": "/recipes/safety/leak-lockdown.tar.gz",
                "changelog": _CHANGELOG,
                **extra,
            }
        ]
    }


def _ingest(hass: HomeAssistant, version: str, **extra: str) -> None:
    get_update_checker(hass).ingest(
        _catalog(version, **extra), base_url=CATALOG_URL, current_version="0.18.0"
    )


# Only the network is faked: extraction, the pre-publish check and the
# move into the bundles dir all run for real.
FETCH = "custom_components.selora_ai.recipes.archive.async_fetch_archive"


def _fake_download(
    tmp_path: Path, **bundle: Any
) -> tuple[Callable[..., Awaitable[Path]], list[str]]:
    """A fetch that serves a real .tar.gz of the fixture as ``bundle``
    describes, and records the URL it was asked for."""
    source = _write_bundle(tmp_path / "archive_src" / SLUG, **bundle)
    archive = tmp_path / "archive_src" / f"{SLUG}.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(source, arcname=SLUG)
    urls: list[str] = []

    async def _fetch(_hass: HomeAssistant, url: str, *, dest_dir: Path) -> Path:
        urls.append(url)
        dest_dir.mkdir(parents=True, exist_ok=True)
        return Path(shutil.copy(archive, dest_dir / archive.name))

    return _fetch, urls


@pytest.fixture
async def installed(hass: HomeAssistant, tmp_path: Path) -> InstallRecord:
    """Leak lockdown installed at v2.0.0 with a non-default input."""
    hass.config.config_dir = str(tmp_path)
    _stage(hass)
    _seed_home(hass)
    result = await async_install(
        hass, slug=SLUG, inputs={"alarm_brightness": 80}, selections=_SELECTION
    )
    assert result.ok, result.punch_list
    assert result.record is not None
    return result.record


# ── Versions and changelog ──────────────────────────────────────────


@pytest.mark.parametrize(
    ("candidate", "installed", "expected"),
    [
        ("1.0.1", "1.0.0", True),
        ("1.1", "1.0.9", True),
        ("v2.0.0", "1.9.9", True),
        ("1.0.0", "1.0.0", False),
        ("1.0", "1.0.0", False),
        ("1.0.0", "1.0.1", False),
        # Unparseable on either side is never an update.
        ("", "1.0.0", False),
        ("1.0.1", "", False),
        ("latest", "1.0.0", False),
    ],
)
def test_is_newer(candidate: str, installed: str, expected: bool) -> None:
    assert is_newer(candidate, installed) is expected


def test_release_summary_is_the_versions_own_section() -> None:
    assert release_summary(_CHANGELOG, "2.1.0") == (
        "Flashes the alarm lights faster, so a leak is noticed from the next room."
    )
    assert release_summary(_CHANGELOG, "2.0.0") == "Initial release."
    assert release_summary(_CHANGELOG, "9.9.9") is None
    assert release_summary("", "2.1.0") is None


def test_release_summary_drops_list_markers() -> None:
    changelog = (
        "## v1.0.0 - 2026-09-23\n\nInitial release.\n\n"
        "- Sends a push notification\n  when the cycle ends.\n* Adds a toggle.\n"
    )
    assert release_summary(changelog, "1.0.0") == (
        "Initial release. Sends a push notification when the cycle ends. Adds a toggle."
    )


def test_release_summary_fits_the_update_entity() -> None:
    changelog = "## v1.0.1\n\n- " + "word " * 100
    summary = release_summary(changelog, "1.0.1")
    assert summary is not None
    assert len(summary) <= 255
    assert summary.endswith("…")


# ── Checker ─────────────────────────────────────────────────────────


async def test_checker_offers_only_newer_compatible_versions(hass: HomeAssistant) -> None:
    checker = get_update_checker(hass)
    signals: list[None] = []
    async_dispatcher_connect(hass, SIGNAL_RECIPE_UPDATES_CHANGED, lambda: signals.append(None))

    _ingest(hass, "2.1.0")
    assert checker.available_version(SLUG, "2.0.0") == "2.1.0"
    assert checker.available_version(SLUG, "2.1.0") is None
    assert checker.available_version("not-in-catalog", "1.0.0") is None
    # Relative package URLs resolve against the catalog, as the panel's do.
    assert checker.entry(SLUG)["package_url"] == (
        "https://selorahomes.com/recipes/safety/leak-lockdown.tar.gz"
    )
    # The same catalog again changes nothing and signals nothing.
    _ingest(hass, "2.1.0")
    await hass.async_block_till_done()
    assert len(signals) == 1

    # A version this integration can't run is not offered.
    _ingest(hass, "3.0.0", min_integration_version="9.0.0")
    assert checker.available_version(SLUG, "2.0.0") is None


async def test_checker_skips_the_catalog_with_nothing_installed(
    hass: HomeAssistant, tmp_path: Path
) -> None:
    hass.config.config_dir = str(tmp_path)
    with patch(
        "custom_components.selora_ai.recipes.updates.async_get_catalog", AsyncMock()
    ) as fetch:
        await get_update_checker(hass).async_refresh()
    fetch.assert_not_awaited()


# ── Applying an update ──────────────────────────────────────────────


async def test_update_reinstalls_with_the_recorded_choices(
    hass: HomeAssistant, installed: InstallRecord, tmp_path: Path
) -> None:
    # An integration the recipe set up at install time: the update must
    # keep the claim, or uninstall stops offering to remove it.
    owned = MockConfigEntry(domain="wake_on_lan")
    owned.add_to_hass(hass)
    store = get_install_store(hass)
    await store.async_record(
        SLUG,
        version=installed.version,
        title=installed.title,
        package_path=installed.package_path,
        bindings=installed.bindings,
        inputs=installed.inputs,
        integrations_installed={"wake_on_lan": owned.entry_id},
    )
    _ingest(hass, "2.1.0")
    download, urls = _fake_download(tmp_path, version="2.1.0")

    with patch(FETCH, download):
        result = await async_update_recipe(hass, SLUG)

    assert result.ok, result.punch_list
    assert urls == ["https://selorahomes.com/recipes/safety/leak-lockdown.tar.gz"]
    record = await store.async_get(SLUG)
    assert record.version == "2.1.0"
    assert record.inputs["alarm_brightness"] == 80
    assert record.bindings == installed.bindings
    assert record.integrations_installed == {"wake_on_lan": owned.entry_id}
    text = package_path(hass, SLUG).read_text(encoding="utf-8")
    assert "v2.1.0" in text
    assert "brightness_pct: 80" in text


async def test_reinstall_drops_claims_on_deleted_entries(
    hass: HomeAssistant, installed: InstallRecord
) -> None:
    store = get_install_store(hass)
    await store.async_record(
        SLUG,
        version=installed.version,
        title=installed.title,
        package_path=installed.package_path,
        bindings=installed.bindings,
        inputs=installed.inputs,
        integrations_installed={"wake_on_lan": "deleted_since"},
    )
    result = await async_install(hass, slug=SLUG, selections=_SELECTION)
    assert result.ok, result.punch_list
    assert (await store.async_get(SLUG)).integrations_installed == {}


async def test_update_needing_a_new_choice_leaves_the_install_alone(
    hass: HomeAssistant, installed: InstallRecord, tmp_path: Path
) -> None:
    before = package_path(hass, SLUG).read_text(encoding="utf-8")
    _ingest(hass, "2.1.0")
    download, _urls = _fake_download(tmp_path, version="2.1.0", extra_integration=True)

    with patch(FETCH, download):
        result = await async_update_recipe(hass, SLUG)

    assert not result.ok
    assert {item.code for item in result.punch_list} == {"integration_missing"}
    assert package_path(hass, SLUG).read_text(encoding="utf-8") == before
    assert (await get_install_store(hass).async_get(SLUG)).version == "2.0.0"


async def test_update_without_a_newer_version_downloads_nothing(
    hass: HomeAssistant, installed: InstallRecord
) -> None:
    download = AsyncMock()
    with (
        patch(
            "custom_components.selora_ai.recipes.updates.async_get_catalog",
            AsyncMock(return_value=_catalog("2.0.0")),
        ),
        patch(FETCH, download),
    ):
        result = await async_update_recipe(hass, SLUG)

    assert not result.ok
    assert result.punch_list[0].code == "no_update"
    download.assert_not_awaited()


async def test_update_refuses_a_package_for_another_recipe(
    hass: HomeAssistant, installed: InstallRecord, tmp_path: Path
) -> None:
    other = _stage(hass, slug="other-recipe")
    other_manifest = (other / "manifest.yaml").read_text(encoding="utf-8")
    _ingest(hass, "2.1.0")
    download, _urls = _fake_download(tmp_path, version="2.1.0", slug="other-recipe")

    with patch(FETCH, download):
        result = await async_update_recipe(hass, SLUG)

    assert result.punch_list[0].code == "download_failed"
    # Rejected before the move: the other recipe's bundle is untouched.
    assert (other / "manifest.yaml").read_text(encoding="utf-8") == other_manifest
    assert (await get_install_store(hass).async_get(SLUG)).version == "2.0.0"


async def test_update_refuses_a_stale_package(
    hass: HomeAssistant, installed: InstallRecord, tmp_path: Path
) -> None:
    """A CDN still serving the old archive must not rewrite the package."""
    bundle_manifest = Path(hass.config.config_dir) / RECIPE_BUNDLE_DIR / SLUG / "manifest.yaml"
    (bundle_manifest.parent / "marker").write_text("kept", encoding="utf-8")
    _ingest(hass, "2.1.0")
    download, _urls = _fake_download(tmp_path, version="2.0.0")

    with patch(FETCH, download):
        result = await async_update_recipe(hass, SLUG)

    assert result.punch_list[0].code == "download_failed"
    assert "not newer" in result.punch_list[0].message
    assert (bundle_manifest.parent / "marker").is_file()


# ── Update entity ───────────────────────────────────────────────────

ENTITY_ID = "update.selora_ai_leak_lockdown"


async def _setup_integration(hass: HomeAssistant) -> MockConfigEntry:
    await async_setup_component(hass, "homeassistant", {})
    await async_setup_component(hass, "http", {})
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENTRY_TYPE: ENTRY_TYPE_LLM,
            "llm_provider": "ollama",
            "ollama_host": "http://127.0.0.1:1",
            "ollama_model": "test-model",
        },
        options={
            CONF_DISCOVERY_ENABLED: False,
            CONF_COLLECTOR_ENABLED: False,
            CONF_INSIGHTS_ENABLED: False,
            "pattern_detection_enabled": False,
        },
    )
    entry.add_to_hass(hass)
    with patch(
        "custom_components.selora_ai.llm_client.LLMClient.health_check",
        AsyncMock(return_value=True),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await asyncio.wait_for(hass.async_block_till_done(), timeout=10)
    return entry


async def test_entity_shows_and_installs_an_update(
    hass: HomeAssistant, installed: InstallRecord, tmp_path: Path
) -> None:
    entry = await _setup_integration(hass)

    state = hass.states.get(ENTITY_ID)
    assert state is not None
    assert state.state == STATE_OFF
    assert state.attributes["installed_version"] == "2.0.0"

    _ingest(hass, "2.1.0")
    await hass.async_block_till_done()
    state = hass.states.get(ENTITY_ID)
    assert state.state == STATE_ON
    assert state.attributes["latest_version"] == "2.1.0"
    assert state.attributes["release_summary"].startswith("Flashes the alarm lights")
    assert state.attributes["release_url"].endswith("/leak-lockdown/")

    download, _urls = _fake_download(tmp_path, version="2.1.0")
    with patch(FETCH, download):
        await hass.services.async_call("update", "install", {"entity_id": ENTITY_ID}, blocking=True)
    await hass.async_block_till_done()
    state = hass.states.get(ENTITY_ID)
    assert state.state == STATE_OFF
    assert state.attributes["installed_version"] == "2.1.0"

    await hass.config_entries.async_unload(entry.entry_id)


async def test_entity_reports_an_update_that_needs_the_wizard(
    hass: HomeAssistant, installed: InstallRecord, tmp_path: Path
) -> None:
    entry = await _setup_integration(hass)
    _ingest(hass, "2.1.0")
    download, _urls = _fake_download(tmp_path, version="2.1.0", extra_integration=True)

    with (
        patch(FETCH, download),
        pytest.raises(HomeAssistantError, match="Selora AI → Recipes"),
    ):
        await hass.services.async_call("update", "install", {"entity_id": ENTITY_ID}, blocking=True)

    await hass.config_entries.async_unload(entry.entry_id)


async def test_entities_follow_installs(hass: HomeAssistant, tmp_path: Path) -> None:
    hass.config.config_dir = str(tmp_path)
    entry = await _setup_integration(hass)
    assert hass.states.get(ENTITY_ID) is None

    _stage(hass)
    _seed_home(hass)
    assert (await async_install(hass, slug=SLUG, selections=_SELECTION)).ok
    await hass.async_block_till_done()
    assert hass.states.get(ENTITY_ID) is not None

    await get_install_store(hass).async_remove(SLUG)
    await hass.async_block_till_done()
    assert hass.states.get(ENTITY_ID) is None

    await hass.config_entries.async_unload(entry.entry_id)


async def test_first_check_waits_for_its_timer(
    hass: HomeAssistant, installed: InstallRecord
) -> None:
    """Setup reads no catalog; the deferred check still does."""
    with patch(
        "custom_components.selora_ai.recipes.updates.async_get_catalog",
        AsyncMock(return_value=_catalog("2.1.0")),
    ) as fetch:
        entry = await _setup_integration(hass)
        fetch.assert_not_awaited()

        async_fire_time_changed(
            hass,
            dt_util.utcnow() + timedelta(seconds=RECIPE_UPDATE_CHECK_INITIAL_DELAY_SECONDS + 1),
        )
        # The check runs as a background task, which a plain wait skips.
        await hass.async_block_till_done(wait_background_tasks=True)

    fetch.assert_awaited()
    assert hass.states.get(ENTITY_ID).state == STATE_ON
    await hass.config_entries.async_unload(entry.entry_id)
