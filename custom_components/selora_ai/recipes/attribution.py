"""Attribute HA automations/scenes back to the Selora recipe that installed them.

Each installed recipe writes a single rendered package file (``package_path``
in its :class:`InstallRecord`). That file on disk is the source of truth for
what Home Assistant actually runs, so we read the automation/scene identifiers
straight from it rather than trusting a stored copy that could drift if the
package were regenerated or hand-edited.

The websocket list handlers use the returned maps to tag each row with the
recipe that owns it (``recipe_slug`` / ``recipe_title``), so the panel can show
a "Recipe: <title>" badge and explain why the row isn't editable in-app.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypedDict

import yaml

from .store import get_install_store

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)


class RecipeRef(TypedDict):
    """The recipe an entity came from — just what the UI badge needs."""

    slug: str
    title: str


class RecipeAttribution(TypedDict):
    """Lookup maps from an entity's stable identifiers to its recipe."""

    automations_by_id: dict[str, RecipeRef]
    automations_by_alias: dict[str, RecipeRef]
    scenes_by_id: dict[str, RecipeRef]
    scenes_by_name: dict[str, RecipeRef]


def _empty() -> RecipeAttribution:
    return {
        "automations_by_id": {},
        "automations_by_alias": {},
        "scenes_by_id": {},
        "scenes_by_name": {},
    }


def _as_list(value: Any) -> list[Any]:
    """HA package sections may be a single mapping or a list of them."""
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _index_package(path: Path, ref: RecipeRef, out: RecipeAttribution) -> None:
    """Parse one rendered package file and record its automation/scene ids."""
    try:
        if not path.is_file():
            return
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        # Best-effort: a missing or malformed package just yields no
        # attribution for its entities, never blocks the list.
        _LOGGER.debug("Could not parse recipe package %s: %s", path, exc)
        return
    if not isinstance(raw, dict):
        return

    for item in _as_list(raw.get("automation")):
        if not isinstance(item, dict):
            continue
        aid = item.get("id")
        if aid is not None:
            out["automations_by_id"][str(aid)] = ref
        alias = item.get("alias")
        if alias:
            out["automations_by_alias"][str(alias)] = ref

    for item in _as_list(raw.get("scene")):
        if not isinstance(item, dict):
            continue
        sid = item.get("id")
        if sid is not None:
            out["scenes_by_id"][str(sid)] = ref
        name = item.get("name")
        if name:
            out["scenes_by_name"][str(name)] = ref


async def async_build_recipe_attribution(hass: HomeAssistant) -> RecipeAttribution:
    """Build entity→recipe lookup maps from every installed recipe's package.

    Returns empty maps when nothing is installed (the common case), so callers
    can unconditionally look up without a None check.
    """
    records = await get_install_store(hass).async_list()
    if not records:
        return _empty()

    def _work() -> RecipeAttribution:
        out = _empty()
        for rec in records:
            if not rec.package_path:
                continue
            ref: RecipeRef = {"slug": rec.slug, "title": rec.title or rec.slug}
            _index_package(Path(rec.package_path), ref, out)
        return out

    return await hass.async_add_executor_job(_work)
