"""Recipes the home has the devices for, as chat-home suggestions.

A catalog recipe is suggested when every role it requires (``min_count``
of one or more) matches an entity in the home, with the same predicate the
install wizard resolves roles with. Optional roles never count: a speaker
that only adds a spoken reminder says nothing about whether the recipe
fits.

How likely the user is to want it is the **specificity of its most
specific required role**. A recipe that needs an Oral-B toothbrush or an LG
fridge door was written for a device this home has; one that needs "any
media player" fits every home and so tells this one nothing. Those score
below ``RECIPE_MIN_SCORE`` and are never suggested: the Recipes tab is
where they are found.

The catalog's ``roles`` are the selectors of each package's manifest,
published so this needs no package download. A recipe listed without them
(an older catalog) cannot be matched and is skipped.

The chat home must not wait on the network, so this reads the cached
catalog however old, and refreshes it in the background when it is stale.
The first chat home after a restart has no recipe rows; the next one does.
"""

from __future__ import annotations

from datetime import timedelta
import logging
from typing import TYPE_CHECKING, Any, Final

from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .recipes.catalog import CatalogError, async_get_catalog, cached_catalog
from .recipes.manifest import ManifestError, RoleSpec
from .recipes.resolver import _entity_satisfies_role
from .recipes.store import get_install_store
from .recipes.version_gate import integration_version, meets_minimum

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from .types import HomeSuggestion, HomeSuggestionEntity

_LOGGER = logging.getLogger(__name__)

RECIPE_PREFIX: Final = "recipe:"

# Specificity of a required role, by the filters it sets. An integration
# names the device's maker; a pattern on top of it names one part of that
# device (the fridge's door, not its ice maker). A device class alone is a
# kind of sensor many homes have; a bare domain is every home.
_SCORE_INTEGRATION_MATCH: Final = 0.85
_SCORE_INTEGRATION: Final = 0.8
_SCORE_MATCH: Final = 0.5
_SCORE_DEVICE_CLASS: Final = 0.4
_SCORE_FEATURES: Final = 0.1
_SCORE_GENERIC: Final = 0.05

# Below this a recipe is too generic to suggest unasked.
RECIPE_MIN_SCORE: Final = 0.3

# A device paired this recently makes its recipes more likely wanted: the
# user is setting it up now.
_RECENT: Final[timedelta] = timedelta(days=14)
_RECENT_BOOST: Final = 0.05

_REFRESH_TASK_KEY: Final = "_recipe_catalog_refresh"


def role_specificity(role: RoleSpec) -> float:
    """How much matching *role* says about this home in particular."""
    if role.integration:
        return _SCORE_INTEGRATION_MATCH if role.match else _SCORE_INTEGRATION
    if role.match:
        return _SCORE_MATCH
    if role.device_class:
        return _SCORE_DEVICE_CLASS
    if role.features:
        return _SCORE_FEATURES
    return _SCORE_GENERIC


def _role_from_catalog(raw: Any) -> RoleSpec | None:
    """A catalog role as the resolver's spec, or ``None`` when it is not
    one this integration can match (an unknown kind, a bad pattern)."""
    if not isinstance(raw, dict):
        return None
    try:
        role = RoleSpec(
            id=str(raw.get("id") or ""),
            kind=str(raw.get("kind") or ""),
            device_class=raw.get("device_class") or None,
            integration=raw.get("integration") or None,
            match=raw.get("match") or None,
            features=tuple(raw.get("features") or ()),
            min_count=int(raw.get("min_count", 1)),
        )
        role.validate()
    except (ManifestError, TypeError, ValueError):
        return None
    return role


def _matches(hass: HomeAssistant, role: RoleSpec, ent_reg: er.EntityRegistry) -> list[str]:
    return sorted(
        state.entity_id
        for state in hass.states.async_all(role.kind)
        if _entity_satisfies_role(role, state, ent_reg)
    )


def score_recipe(
    hass: HomeAssistant, entry: dict[str, Any], ent_reg: er.EntityRegistry
) -> tuple[float, list[str]] | None:
    """The recipe's score and the entities its deciding role matched, or
    ``None`` when the home lacks a device it requires or the catalog does
    not say what it requires."""
    raw_roles = entry.get("roles")
    if not isinstance(raw_roles, list):
        return None
    roles = [_role_from_catalog(r) for r in raw_roles]
    if any(r is None for r in roles):
        return None
    required = [r for r in roles if r is not None and r.min_count >= 1]
    if not required:
        # Runs in any home: nothing about this one makes it more likely.
        return _SCORE_GENERIC, []
    best = -1.0
    deciding: list[str] = []
    for role in required:
        found = _matches(hass, role, ent_reg)
        if len(found) < role.min_count:
            return None
        if (score := role_specificity(role)) > best:
            best, deciding = score, found
    cutoff = dt_util.utcnow() - _RECENT
    if any(
        (ent := ent_reg.async_get(entity_id)) is not None and ent.created_at > cutoff
        for entity_id in deciding
    ):
        best += _RECENT_BOOST
    return min(best, 1.0), deciding


def _entity(hass: HomeAssistant, entity_id: str) -> HomeSuggestionEntity:
    state = hass.states.get(entity_id)
    name = state.attributes.get("friendly_name") if state else None
    return {"entity_id": entity_id, "name": str(name or entity_id)}


def _refresh_in_background(hass: HomeAssistant) -> None:
    domain_data = hass.data.setdefault(DOMAIN, {})
    running = domain_data.get(_REFRESH_TASK_KEY)
    if running is not None and not running.done():
        return

    async def _refresh() -> None:
        try:
            await async_get_catalog(hass)
        except CatalogError as exc:
            _LOGGER.debug("Recipe catalog refresh for suggestions failed: %s", exc)

    domain_data[_REFRESH_TASK_KEY] = hass.async_create_background_task(
        _refresh(), f"{DOMAIN}_recipe_suggestions_catalog"
    )


async def async_recipe_suggestions(hass: HomeAssistant, put_away: set[str]) -> list[HomeSuggestion]:
    """A row per catalog recipe this home has the devices for, scored,
    without installed recipes, the ones in *put_away*, and those too
    generic to suggest."""
    catalog, fresh = cached_catalog()
    if not fresh:
        _refresh_in_background(hass)
    if catalog is None:
        return []
    installed = {r.slug for r in await get_install_store(hass).async_list()}
    current = await hass.async_add_executor_job(integration_version)
    ent_reg = er.async_get(hass)
    rows: list[HomeSuggestion] = []
    for entry in catalog.get("recipes") or []:
        if not isinstance(entry, dict) or not (slug := str(entry.get("slug") or "")):
            continue
        suggestion_id = f"{RECIPE_PREFIX}{slug}"
        if slug in installed or suggestion_id in put_away:
            continue
        if not meets_minimum(current, str(entry.get("min_integration_version") or "")):
            continue
        scored = score_recipe(hass, entry, ent_reg)
        if scored is None or scored[0] < RECIPE_MIN_SCORE:
            continue
        score, entity_ids = scored
        title = str(entry.get("title") or slug)
        rows.append(
            {
                "suggestion_id": suggestion_id,
                "type": "recipe",
                "entities": [_entity(hass, e) for e in entity_ids],
                "collection": {"source": "recipe", "name": title, "ref": slug},
                "recipe": {
                    "slug": slug,
                    "title": title,
                    "description": str(entry.get("description") or ""),
                },
                "score": score,
            }
        )
    rows.sort(key=lambda r: -r["score"])
    return rows
