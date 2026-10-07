"""One update entity per installed recipe.

Puts a newer catalog version of a recipe in Settings → Updates, next to
core and HACS, with its changelog and an Install button that re-renders
the package with the choices the recipe was installed with. The work is
in ``recipes/updates.py``; this module only mirrors the install records
as entities and translates a halted update into an error HA can show.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from homeassistant.components.update import UpdateEntity, UpdateEntityFeature
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_connect

from .recipes.const import SIGNAL_RECIPE_INSTALLS_CHANGED, SIGNAL_RECIPE_UPDATES_CHANGED
from .recipes.store import get_install_store
from .recipes.updates import async_update_recipe, get_update_checker, release_summary
from .sensor import _hub_device_info

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from .recipes.store import InstallRecord

_LOGGER = logging.getLogger(__name__)

_UNIQUE_ID_PREFIX = "selora_ai_recipe_"

# Punch-list codes that mean the new version needs a choice the old
# install never made, rather than that something broke.
_NEEDS_INPUT_CODES = frozenset(
    {"role_unmet", "binding_pending", "input_invalid", "integration_missing"}
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Add an entity per install record and keep the set in step with
    installs and uninstalls for as long as the entry is loaded."""
    store = get_install_store(hass)
    checker = get_update_checker(hass)
    entities: dict[str, RecipeUpdateEntity] = {}

    records = await store.async_list()
    installed = {record.slug for record in records}
    # A recipe uninstalled while the entry was unloaded left its entity
    # in the registry; nothing else will remove it.
    registry = er.async_get(hass)
    for reg_entry in er.async_entries_for_config_entry(registry, entry.entry_id):
        if (
            reg_entry.domain == "update"
            and reg_entry.unique_id.startswith(_UNIQUE_ID_PREFIX)
            and reg_entry.unique_id.removeprefix(_UNIQUE_ID_PREFIX) not in installed
        ):
            registry.async_remove(reg_entry.entity_id)

    for record in records:
        entities[record.slug] = RecipeUpdateEntity(record)
    async_add_entities(list(entities.values()))

    async def _installs_changed(slug: str) -> None:
        record = await store.async_get(slug)
        entity = entities.get(slug)
        if record is None:
            if entity is None:
                return
            entities.pop(slug)
            if entity.entity_id:
                er.async_get(hass).async_remove(entity.entity_id)
            return
        if entity is None:
            entities[slug] = RecipeUpdateEntity(record)
            async_add_entities([entities[slug]])
            return
        entity.set_record(record)

    entry.async_on_unload(
        async_dispatcher_connect(hass, SIGNAL_RECIPE_INSTALLS_CHANGED, _installs_changed)
    )

    checker.async_start()
    entry.async_on_unload(checker.async_stop)


class RecipeUpdateEntity(UpdateEntity):
    """The installed version of one recipe against the catalog's newest."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_icon = "mdi:book-open-variant"

    def __init__(self, record: InstallRecord) -> None:
        self._record = record
        self._attr_unique_id = f"{_UNIQUE_ID_PREFIX}{record.slug}"
        self._attr_name = record.title or record.slug
        # The full hub identity, not just its identifier: platforms set up
        # concurrently, and an entity that only references the hub before
        # the sensors have created it is left without a device.
        self._attr_device_info = _hub_device_info()

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, SIGNAL_RECIPE_UPDATES_CHANGED, self._catalog_changed
            )
        )

    @callback
    def set_record(self, record: InstallRecord) -> None:
        self._record = record
        if self.hass is not None and self.entity_id:
            self.async_write_ha_state()

    @callback
    def _catalog_changed(self) -> None:
        self.async_write_ha_state()

    def _catalog_entry(self) -> dict[str, Any] | None:
        if self.hass is None:
            return None
        return get_update_checker(self.hass).entry(self._record.slug)

    @property
    def title(self) -> str | None:
        return self._record.title or self._record.slug

    @property
    def installed_version(self) -> str | None:
        return self._record.version or None

    @property
    def latest_version(self) -> str | None:
        """The catalog's version when it is newer, else the installed one:
        a recipe the catalog doesn't list, or lists at a version this
        integration can't run, is simply up to date."""
        if self.hass is None:
            return self.installed_version
        newer = get_update_checker(self.hass).available_version(
            self._record.slug, self._record.version
        )
        return newer or self.installed_version

    @property
    def supported_features(self) -> UpdateEntityFeature:
        features = UpdateEntityFeature.INSTALL
        entry = self._catalog_entry()
        if entry and entry.get("changelog"):
            features |= UpdateEntityFeature.RELEASE_NOTES
        return features

    @property
    def release_summary(self) -> str | None:
        entry = self._catalog_entry()
        if not entry or self.latest_version == self.installed_version:
            return None
        return release_summary(str(entry.get("changelog") or ""), str(entry.get("version") or ""))

    @property
    def release_url(self) -> str | None:
        entry = self._catalog_entry()
        return str(entry["url"]) if entry and entry.get("url") else None

    async def async_release_notes(self) -> str | None:
        entry = self._catalog_entry()
        if entry is None:
            return None
        return str(entry.get("changelog") or "") or None

    async def async_install(self, version: str | None, backup: bool, **kwargs: Any) -> None:
        result = await async_update_recipe(self.hass, self._record.slug)
        if result.ok:
            return
        codes = {item.code for item in result.punch_list}
        if codes & _NEEDS_INPUT_CODES:
            raise HomeAssistantError(
                f"This version of {self.title} needs a choice the current install "
                "didn't make. Open Selora AI → Recipes to finish the update."
            )
        reasons = "; ".join(item.message for item in result.punch_list) or result.stage_reached
        raise HomeAssistantError(f"Could not update {self.title}: {reasons}")
