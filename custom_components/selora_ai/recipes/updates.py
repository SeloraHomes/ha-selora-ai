"""Find newer versions of installed recipes and apply them in place.

An installed recipe is a rendered package plus an install record holding
the devices and inputs it was rendered with. A new version on the catalog
only reaches a home when the package is rendered again, and nothing did
that short of running the whole wizard a second time. This module closes
the gap: :class:`RecipeUpdateChecker` reads the catalog on a timer and
knows the newest compatible version of every recipe, and
:func:`async_update_recipe` re-runs the install with the record's own
choices, so an update is one action rather than a reconfiguration.

The checker reads the catalog only while at least one recipe is
installed: a home with none has nothing to update, and no reason to call
selorahomes.com every few hours.

An update that cannot be applied unattended (the new version wants a
device role, an input or an integration the old install didn't have)
halts before anything is written, with the pipeline's punch list. The
running package stays as it was; the panel's wizard is where the
homeowner supplies what is missing.
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import datetime, timedelta
import logging
import re
from typing import TYPE_CHECKING, Any
from urllib.parse import urljoin

from homeassistant.core import callback
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_call_later, async_track_time_interval

from .archive import ArchiveError, async_install_from_url
from .catalog import CatalogError, _catalog_url, async_get_catalog
from .const import (
    RECIPE_UPDATE_CHECK_INITIAL_DELAY_SECONDS,
    RECIPE_UPDATE_CHECK_INTERVAL_HOURS,
    SIGNAL_RECIPE_UPDATES_CHANGED,
)
from .dashboard import SKIP_TARGET
from .pipeline import PipelineResult, PunchItem, async_install
from .store import get_install_store
from .version_gate import integration_version, is_newer, meets_minimum

if TYPE_CHECKING:
    from collections.abc import Callable

    from homeassistant.core import HomeAssistant

    from .manifest import Manifest

_LOGGER = logging.getLogger(__name__)

_HASS_DATA_KEY = "_selora_ai_recipe_update_checker"

# HA's update entity caps ``release_summary`` at 255 characters.
_RELEASE_SUMMARY_MAX = 255

# A changelog version heading: "## v1.0.1 - 2026-10-06", "## 1.0.1".
_CHANGELOG_HEADING_RE = re.compile(r"^##\s+v?(\d[\w.\-]*)", re.MULTILINE)


class RecipeUpdateChecker:
    """The newest compatible catalog entry for every recipe.

    Entries are raw catalog JSON, kept whole so the update entity can
    read the changelog and page URL from the same place the panel does.
    ``package_url`` is made absolute on the way in, as the catalog WS
    does: the Hugo dev server lists it relative to the catalog.
    """

    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass
        self._entries: dict[str, dict[str, Any]] = {}
        self._unsub_initial: Callable[[], None] | None = None
        self._unsub_interval: Callable[[], None] | None = None
        self._tasks: set[asyncio.Task[None]] = set()

    def entry(self, slug: str) -> dict[str, Any] | None:
        """The catalog entry for ``slug``, or ``None`` when the catalog
        does not list it (side-loaded) or lists a version this
        integration is too old to run."""
        return self._entries.get(slug)

    def available_version(self, slug: str, installed: str) -> str | None:
        """The catalog version of ``slug`` when it is newer than
        ``installed``, else ``None``."""
        entry = self._entries.get(slug)
        if entry is None:
            return None
        version = str(entry.get("version") or "")
        return version if is_newer(version, installed) else None

    def ingest(self, catalog: dict[str, Any], *, base_url: str, current_version: str) -> None:
        """Take a fetched catalog as the new truth.

        Called by the periodic check and by the panel's own catalog read,
        so the update entities and the Recipes tab never disagree about
        what is available. Signals only when something changed.
        """
        entries: dict[str, dict[str, Any]] = {}
        for raw in catalog.get("recipes") or []:
            if not isinstance(raw, dict) or not raw.get("slug"):
                continue
            if not meets_minimum(current_version, str(raw.get("min_integration_version") or "")):
                continue
            entry = dict(raw)
            if entry.get("package_url"):
                entry["package_url"] = urljoin(base_url, str(entry["package_url"]))
            entries[str(entry["slug"])] = entry
        if entries == self._entries:
            return
        self._entries = entries
        async_dispatcher_send(self._hass, SIGNAL_RECIPE_UPDATES_CHANGED)

    async def async_refresh(self, *, force: bool = False) -> None:
        """Read the catalog and ingest it. A failed read keeps what the
        last good one said: an outage is not "no updates"."""
        if not await get_install_store(self._hass).async_list():
            return
        try:
            catalog = await async_get_catalog(self._hass, force_refresh=force)
        except CatalogError as exc:
            _LOGGER.debug("Recipe update check skipped: %s", exc)
            return
        current = await self._hass.async_add_executor_job(integration_version)
        self.ingest(catalog, base_url=_catalog_url(), current_version=current)

    @callback
    def async_start(self) -> None:
        """Arm the first check and the periodic one. Timers, not a
        sleeping task: setup must leave nothing for bootstrap to wait on."""
        if self._unsub_interval is not None:
            return
        self._unsub_initial = async_call_later(
            self._hass, RECIPE_UPDATE_CHECK_INITIAL_DELAY_SECONDS, self._initial_check
        )
        self._unsub_interval = async_track_time_interval(
            self._hass,
            self._periodic_check,
            timedelta(hours=RECIPE_UPDATE_CHECK_INTERVAL_HOURS),
        )

    async def async_stop(self) -> None:
        if self._unsub_initial is not None:
            self._unsub_initial()
            self._unsub_initial = None
        if self._unsub_interval is not None:
            self._unsub_interval()
            self._unsub_interval = None
        pending = [t for t in self._tasks if not t.done()]
        for task in pending:
            task.cancel()
        for task in pending:
            with suppress(asyncio.CancelledError):
                await task
        self._tasks.clear()

    @callback
    def _initial_check(self, _now: datetime) -> None:
        self._unsub_initial = None
        self._spawn("selora_ai_recipe_update_check")

    @callback
    def _periodic_check(self, _now: datetime) -> None:
        self._spawn("selora_ai_recipe_update_check")

    @callback
    def _spawn(self, name: str) -> None:
        task = self._hass.async_create_background_task(self.async_refresh(), name=name)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)


def get_update_checker(hass: HomeAssistant) -> RecipeUpdateChecker:
    """Singleton accessor, like the install store: one per HA instance."""
    domain_data = hass.data.setdefault("selora_ai", {})
    checker = domain_data.get(_HASS_DATA_KEY)
    if checker is None:
        checker = RecipeUpdateChecker(hass)
        domain_data[_HASS_DATA_KEY] = checker
    return checker


def changelog_section(changelog: str, version: str) -> str:
    """The body of ``version``'s section in a recipe CHANGELOG.md, or
    ``""`` when it has none. Used as the update's release summary."""
    headings = list(_CHANGELOG_HEADING_RE.finditer(changelog or ""))
    for index, heading in enumerate(headings):
        if heading.group(1) != version:
            continue
        end = headings[index + 1].start() if index + 1 < len(headings) else len(changelog)
        return changelog[heading.end() : end].split("\n", 1)[-1].strip()
    return ""


def changelog_since(changelog: str, installed: str, latest: str) -> str:
    """The sections of a recipe CHANGELOG.md for versions after
    ``installed`` up to ``latest``: what the update brings, as core shows
    an add-on's. Versions compare like :func:`is_newer`, so ``v2.0`` and
    ``2.0.0`` are one release. The whole changelog when no section fits."""
    headings = list(_CHANGELOG_HEADING_RE.finditer(changelog or ""))
    starts = [heading.start() for heading in headings] + [len(changelog)]
    sections = [
        changelog[start:end].strip()
        for heading, start, end in zip(headings, starts, starts[1:], strict=False)
        if is_newer(heading.group(1), installed) and not is_newer(heading.group(1), latest)
    ]
    return "\n\n".join(sections) or changelog


def release_summary(changelog: str, version: str) -> str | None:
    """``version``'s changelog section flattened to one line that fits
    HA's release summary, or ``None`` when the changelog has nothing.
    List markers are dropped: joined onto one line they read as stray
    dashes ("Initial release. - Sends…")."""
    lines = (
        line.strip().removeprefix("- ").removeprefix("* ")
        for line in changelog_section(changelog, version).splitlines()
    )
    section = " ".join(" ".join(lines).split())
    if not section:
        return None
    if len(section) <= _RELEASE_SUMMARY_MAX:
        return section
    return section[: _RELEASE_SUMMARY_MAX - 1].rstrip() + "…"


def _halt(code: str, message: str) -> PipelineResult:
    return PipelineResult(
        ok=False,
        stage_reached="definition",
        punch_list=(PunchItem(stage="definition", code=code, message=message),),
    )


async def async_update_recipe(
    hass: HomeAssistant,
    slug: str,
    *,
    on_event: Callable[[dict[str, Any]], None] | None = None,
) -> PipelineResult:
    """Install the catalog's newer version of ``slug`` over the current one.

    Downloads the new bundle, then runs the ordinary install with the
    record's bindings and inputs. Inputs a resolver fills (a TV's MAC)
    are worked out again, as on a first install. The dashboard step is
    skipped: the card already placed keeps pointing at the same
    entities, and the record carries its placement forward.

    The download replaces the bundle on disk before the install runs, so
    an update that halts leaves the new bundle staged for the wizard to
    finish, while the package HA runs is still the old one.
    """
    record = await get_install_store(hass).async_get(slug)
    if record is None:
        return _halt("not_installed", f"Recipe {slug!r} is not installed.")

    checker = get_update_checker(hass)
    if checker.available_version(slug, record.version) is None:
        # The entity or the panel may be acting on a catalog read the
        # periodic check hasn't caught up with; confirm before giving up.
        await checker.async_refresh(force=True)
    target_version = checker.available_version(slug, record.version)
    entry = checker.entry(slug)
    if target_version is None or entry is None or not entry.get("package_url"):
        return _halt(
            "no_update",
            f"No newer version of {record.title or slug} is available.",
        )

    def _is_this_update(manifest: Manifest) -> None:
        # Checked before the bundle replaces anything on disk: an archive
        # for another recipe would overwrite that recipe's bundle, and a
        # stale one (a CDN behind the catalog) would rewrite the running
        # package with the version it already has, or an older one.
        if manifest.slug != slug:
            raise ArchiveError(
                f"The catalog's package for {slug!r} contains recipe {manifest.slug!r}."
            )
        if not is_newer(manifest.version, record.version):
            raise ArchiveError(
                f"The catalog's package for {slug!r} is v{manifest.version}, "
                f"not newer than the installed v{record.version}."
            )

    try:
        staged = await async_install_from_url(
            hass, str(entry["package_url"]), check=_is_this_update
        )
    except ArchiveError as exc:
        return _halt("download_failed", str(exc))

    _LOGGER.info("Updating recipe %s from v%s to v%s", slug, record.version, staged.version)
    return await async_install(
        hass,
        slug=slug,
        inputs=dict(record.inputs),
        selections={role: list(ids) for role, ids in record.bindings.items()},
        dashboard_target=SKIP_TARGET,
        on_event=on_event,
    )
