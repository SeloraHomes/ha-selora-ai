"""Tests for recipes suggested on the chat home.

The catalog is seeded straight into the cache, as a fetch would leave it,
so nothing touches the network. Entities are registered under the platform
a recipe's role names, which is what the integration filter matches.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import timedelta
from time import monotonic
from typing import Any
from unittest.mock import AsyncMock, patch

from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
import pytest

from custom_components.selora_ai import recipe_suggestions as rs
from custom_components.selora_ai.home_suggestions import (
    async_dismiss_home_suggestion,
    async_home_suggestions,
)
from custom_components.selora_ai.recipes import catalog


def _recipe(slug: str, *roles: dict[str, Any], **extra: Any) -> dict[str, Any]:
    return {"slug": slug, "title": slug.replace("-", " ").title(), "roles": list(roles), **extra}


BRUSHING = _recipe(
    "kids-brushing-check",
    {"id": "brushing_timers", "kind": "sensor", "integration": "oralb", "device_class": "duration"},
    # Optional: a home with speakers is no likelier to want it.
    {"id": "announce_speakers", "kind": "media_player", "min_count": 0},
    description="Notices a missed brushing session.",
)
FRIDGE_DOOR = _recipe(
    "fridge-door-alert",
    {
        "id": "doors",
        "kind": "binary_sensor",
        "device_class": "door",
        "integration": "lg_thinq",
        "match": "refriger|fridge|freezer",
    },
)
PAUSE_ON_DOORBELL = _recipe(
    "pause-media-on-doorbell",
    {"id": "doorbell", "kind": "binary_sensor"},
    {"id": "players", "kind": "media_player"},
)
LEAK_ALERT = _recipe(
    "water-leak-alert",
    {"id": "leaks", "kind": "binary_sensor", "device_class": "moisture"},
)


@pytest.fixture
def seed_catalog() -> Iterator[Any]:
    def seed(*recipes: dict[str, Any]) -> None:
        catalog._cache_payload = {"recipes": list(recipes)}
        catalog._cache_at = monotonic()
        catalog._cache_url = catalog._catalog_url()

    yield seed
    catalog.invalidate_cache()


def _add(
    hass: HomeAssistant,
    entity_id: str,
    platform: str = "test",
    device_class: str | None = None,
    name: str | None = None,
) -> str:
    domain, object_id = entity_id.split(".")
    entry = er.async_get(hass).async_get_or_create(
        domain,
        platform,
        object_id,
        suggested_object_id=object_id,
        original_device_class=device_class,
    )
    assert entry.entity_id == entity_id
    hass.states.async_set(entity_id, "0", {"friendly_name": name or object_id.replace("_", " ")})
    return entity_id


async def _recipes(hass: HomeAssistant) -> list[dict[str, Any]]:
    return [s for s in await async_home_suggestions(hass) if s["type"] == "recipe"]


async def test_a_recipe_for_a_device_the_home_has_is_suggested(
    hass: HomeAssistant, seed_catalog: Any
) -> None:
    seed_catalog(BRUSHING)
    brush = _add(hass, "sensor.kids_brush_duration", "oralb", "duration")

    [row] = await _recipes(hass)

    assert row["suggestion_id"] == "recipe:kids-brushing-check"
    assert row["recipe"] == {
        "slug": "kids-brushing-check",
        "title": "Kids Brushing Check",
        "description": "Notices a missed brushing session.",
    }
    assert [e["entity_id"] for e in row["entities"]] == [brush]
    assert row["score"] >= rs.RECIPE_MIN_SCORE


async def test_a_device_specific_recipe_outranks_a_device_class_one(
    hass: HomeAssistant, seed_catalog: Any
) -> None:
    seed_catalog(LEAK_ALERT, BRUSHING, FRIDGE_DOOR)
    _add(hass, "binary_sensor.sink_leak", device_class="moisture")
    _add(hass, "sensor.kids_brush_duration", "oralb", "duration")
    _add(hass, "binary_sensor.fridge_door", "lg_thinq", "door", "Refrigerator door")

    rows = await _recipes(hass)

    assert [r["recipe"]["slug"] for r in rows] == [
        "fridge-door-alert",
        "kids-brushing-check",
        "water-leak-alert",
    ]


async def test_a_recipe_any_home_fits_is_never_suggested(
    hass: HomeAssistant, seed_catalog: Any
) -> None:
    seed_catalog(
        PAUSE_ON_DOORBELL,
        _recipe("health-alert", {"id": "s", "kind": "media_player", "min_count": 0}),
    )
    _add(hass, "binary_sensor.doorbell")
    _add(hass, "media_player.living_room")

    assert await _recipes(hass) == []


async def test_a_recipe_missing_a_required_device_is_not_suggested(
    hass: HomeAssistant, seed_catalog: Any
) -> None:
    seed_catalog(BRUSHING, FRIDGE_DOOR)
    # A duration sensor, but not from an Oral-B brush.
    _add(hass, "sensor.dishwasher_remaining", "lg_thinq", "duration")
    # An LG door that is the oven's, not the fridge's.
    _add(hass, "binary_sensor.oven_door", "lg_thinq", "door", "Oven door")

    assert await _recipes(hass) == []


async def test_a_catalog_without_roles_suggests_nothing(
    hass: HomeAssistant, seed_catalog: Any
) -> None:
    seed_catalog({"slug": "kids-brushing-check", "title": "Brushing"})
    _add(hass, "sensor.kids_brush_duration", "oralb", "duration")

    assert await _recipes(hass) == []


async def test_installed_recipes_and_dismissed_rows_are_left_out(
    hass: HomeAssistant, seed_catalog: Any
) -> None:
    seed_catalog(BRUSHING, FRIDGE_DOOR)
    _add(hass, "sensor.kids_brush_duration", "oralb", "duration")
    _add(hass, "binary_sensor.fridge_door", "lg_thinq", "door", "Fridge door")
    installed = AsyncMock(return_value=[type("Record", (), {"slug": "fridge-door-alert"})()])

    with patch.object(rs, "get_install_store") as store:
        store.return_value.async_list = installed
        await async_dismiss_home_suggestion(hass, "recipe:kids-brushing-check")
        assert await _recipes(hass) == []


async def test_a_recipe_too_new_for_this_integration_is_left_out(
    hass: HomeAssistant, seed_catalog: Any
) -> None:
    seed_catalog({**BRUSHING, "min_integration_version": "999.0.0"})
    _add(hass, "sensor.kids_brush_duration", "oralb", "duration")

    assert await _recipes(hass) == []


async def test_a_device_paired_recently_raises_its_recipes(
    hass: HomeAssistant, seed_catalog: Any, freezer: FrozenDateTimeFactory
) -> None:
    seed_catalog(BRUSHING)
    _add(hass, "sensor.kids_brush_duration", "oralb", "duration")
    [fresh] = await _recipes(hass)

    freezer.tick(timedelta(days=30))
    [settled] = await _recipes(hass)

    assert fresh["score"] > settled["score"]


async def test_every_row_is_scored_and_sorted(hass: HomeAssistant, seed_catalog: Any) -> None:
    seed_catalog(BRUSHING)
    _add(hass, "sensor.kids_brush_duration", "oralb", "duration")

    rows = await async_home_suggestions(hass)

    scores = [r["score"] for r in rows]
    assert scores == sorted(scores, reverse=True)


async def test_no_cached_catalog_refreshes_in_the_background(hass: HomeAssistant) -> None:
    catalog.invalidate_cache()
    with patch.object(rs, "async_get_catalog", AsyncMock()) as fetch:
        assert await _recipes(hass) == []
        await hass.async_block_till_done(wait_background_tasks=True)
    fetch.assert_awaited_once()


def test_specificity_follows_the_filters_a_role_sets() -> None:
    def spec(**kw: Any) -> float:
        role = rs._role_from_catalog({"id": "r", "kind": "binary_sensor", **kw})
        assert role is not None
        return rs.role_specificity(role)

    assert (
        spec(integration="lg_thinq", match="fridge")
        > spec(integration="oralb")
        > spec(device_class="moisture")
        >= rs.RECIPE_MIN_SCORE
        > spec()
    )
