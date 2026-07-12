"""Tests for recipe attribution — mapping automations/scenes back to the
recipe whose package installed them, by parsing the package file on disk.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from custom_components.selora_ai.recipes.attribution import (
    async_build_recipe_attribution,
)
from custom_components.selora_ai.recipes.store import get_install_store

pytestmark = pytest.mark.asyncio

_PACKAGE_YAML = """\
automation:
  - id: selora_recipe_leak_lockdown_all_clear
    alias: "Leak Lockdown — all clear"
    mode: single
    trigger: []
    action: []
scene:
  - id: selora_recipe_leak_lockdown_alarm
    name: "Leak Lockdown — alarm"
    entities: {}
"""


async def _record_recipe(hass, tmp_path: Path, *, yaml_text: str = _PACKAGE_YAML) -> Path:
    pkg = tmp_path / "leak_lockdown.yaml"
    pkg.write_text(yaml_text, encoding="utf-8")
    store = get_install_store(hass)
    await store.async_record(
        "leak-lockdown",
        version="1.0.0",
        title="Leak Lockdown",
        package_path=str(pkg),
        bindings={},
        inputs={},
    )
    return pkg


async def test_empty_when_nothing_installed(hass) -> None:
    attribution = await async_build_recipe_attribution(hass)
    assert attribution == {
        "automations_by_id": {},
        "automations_by_alias": {},
        "scenes_by_id": {},
        "scenes_by_name": {},
    }


async def test_indexes_automations_and_scenes(hass, tmp_path) -> None:
    await _record_recipe(hass, tmp_path)
    attribution = await async_build_recipe_attribution(hass)

    ref = {"slug": "leak-lockdown", "title": "Leak Lockdown"}
    assert (
        attribution["automations_by_id"]["selora_recipe_leak_lockdown_all_clear"] == ref
    )
    assert attribution["automations_by_alias"]["Leak Lockdown — all clear"] == ref
    assert attribution["scenes_by_id"]["selora_recipe_leak_lockdown_alarm"] == ref
    assert attribution["scenes_by_name"]["Leak Lockdown — alarm"] == ref


async def test_missing_package_file_is_skipped(hass, tmp_path) -> None:
    # Record points at a path that doesn't exist — must not raise.
    store = get_install_store(hass)
    await store.async_record(
        "ghost",
        version="1.0.0",
        title="Ghost",
        package_path=str(tmp_path / "does_not_exist.yaml"),
        bindings={},
        inputs={},
    )
    attribution = await async_build_recipe_attribution(hass)
    assert attribution["automations_by_id"] == {}


async def test_malformed_yaml_is_skipped(hass, tmp_path) -> None:
    await _record_recipe(hass, tmp_path, yaml_text="automation: [unterminated")
    attribution = await async_build_recipe_attribution(hass)
    # Parse failure yields no attribution but doesn't crash.
    assert attribution["automations_by_id"] == {}


async def test_title_falls_back_to_slug(hass, tmp_path) -> None:
    pkg = tmp_path / "noname.yaml"
    pkg.write_text(
        'automation:\n  - id: selora_recipe_noname_x\n    alias: "X"\n',
        encoding="utf-8",
    )
    store = get_install_store(hass)
    await store.async_record(
        "noname",
        version="1.0.0",
        title="",
        package_path=str(pkg),
        bindings={},
        inputs={},
    )
    attribution = await async_build_recipe_attribution(hass)
    assert attribution["automations_by_id"]["selora_recipe_noname_x"]["title"] == "noname"
