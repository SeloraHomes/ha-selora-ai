"""Persist install records: which recipes are installed at what version,
with what role bindings and input values.

The package file on disk is the source of truth for what HA actually
runs. The install record is metadata ABOUT that file — last bindings,
last inputs, install timestamp — so the wizard can show "you installed
this on date X with these devices" and the upgrade flow can diff.

Uninstall removes both the package file and the record. They're kept
in lockstep by the pipeline; nothing else writes here.
"""

from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
import logging
from typing import TYPE_CHECKING, Any

from homeassistant.helpers.storage import Store

from .const import INSTALL_STORE_KEY, INSTALL_STORE_VERSION

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class InstallRecord:
    """One installed recipe's metadata. Mirrors what the wizard needs to
    render the "Installed" list — no behaviour, just data.
    """

    slug: str
    version: str
    title: str
    installed_at: str  # ISO8601 UTC
    package_path: str  # absolute path; useful for "open in editor"
    bindings: dict[str, list[str]] = field(default_factory=dict)
    inputs: dict[str, Any] = field(default_factory=dict)
    # Config entries this recipe's auto_setup created during install.
    # Maps integration domain → HA config entry id. Used by uninstall
    # to offer "also remove these integrations" with a clear ownership
    # claim — we never offer to remove an entry the user set up before
    # the recipe ran.
    integrations_installed: dict[str, str] = field(default_factory=dict)
    # Outcome of the optional final-stage dashboard card insertion.
    # Shape: ``{"ok": bool, "reason": str, "target": str|None,
    # "view": int|str|None}``. Empty when the recipe declared no
    # ``dashboard:`` block. The wizard reads this to show "added to your
    # dashboard" (or a fall-back-to-manual hint); uninstall removes the
    # tagged card regardless of this record.
    dashboard_card: dict[str, Any] = field(default_factory=dict)


def _from_dict(data: dict[str, Any]) -> InstallRecord:
    return InstallRecord(
        slug=str(data.get("slug", "")),
        version=str(data.get("version", "")),
        title=str(data.get("title", "")),
        installed_at=str(data.get("installed_at", "")),
        package_path=str(data.get("package_path", "")),
        bindings=dict(data.get("bindings") or {}),
        inputs=dict(data.get("inputs") or {}),
        integrations_installed=dict(data.get("integrations_installed") or {}),
        dashboard_card=dict(data.get("dashboard_card") or {}),
    )


class InstallStore:
    """Async wrapper around HA's :class:`Store` for the recipe install
    records. Mutations are serialised on a per-instance lock so two
    concurrent installs (or an install + uninstall) can't interleave
    read-modify-write on the in-memory dict.
    """

    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass
        self._store: Store = Store(hass, version=INSTALL_STORE_VERSION, key=INSTALL_STORE_KEY)
        self._data: dict[str, dict[str, Any]] | None = None
        self._mutex = asyncio.Lock()

    async def async_load(self) -> None:
        raw = await self._store.async_load()
        self._data = raw if isinstance(raw, dict) else {}

    async def _ensure_loaded(self) -> dict[str, dict[str, Any]]:
        if self._data is None:
            await self.async_load()
        assert self._data is not None
        return self._data

    async def _save(self) -> None:
        if self._data is not None:
            await self._store.async_save(self._data)

    async def async_record(
        self,
        slug: str,
        *,
        version: str,
        title: str,
        package_path: str,
        bindings: dict[str, list[str]],
        inputs: dict[str, Any],
        integrations_installed: dict[str, str] | None = None,
        dashboard_card: dict[str, Any] | None = None,
    ) -> InstallRecord:
        """Idempotent upsert. Re-installing overwrites the existing
        record — bindings/inputs reflect what the just-completed
        install actually used, not whatever was in storage from a
        prior run.
        """
        async with self._mutex:
            data = await self._ensure_loaded()
            record = InstallRecord(
                slug=slug,
                version=version,
                title=title,
                installed_at=datetime.now(UTC).isoformat(),
                package_path=package_path,
                bindings=dict(bindings),
                inputs=dict(inputs),
                integrations_installed=dict(integrations_installed or {}),
                dashboard_card=dict(dashboard_card or {}),
            )
            data[slug] = asdict(record)
            await self._save()
            _LOGGER.info("Recorded recipe install: %s v%s", slug, version)
            return record

    async def async_update_dashboard_card(
        self, slug: str, dashboard_card: dict[str, Any]
    ) -> InstallRecord | None:
        """Patch just the ``dashboard_card`` field on an existing record.

        The install pipeline records the card it placed; this lets the
        post-install "add a card" action (wizard Step 5) update that
        outcome when the user places a card after the install already ran.
        Returns ``None`` if the recipe has no record.
        """
        async with self._mutex:
            data = await self._ensure_loaded()
            raw = data.get(slug)
            if raw is None:
                return None
            raw["dashboard_card"] = dict(dashboard_card)
            await self._save()
            return _from_dict(raw)

    async def async_get(self, slug: str) -> InstallRecord | None:
        data = await self._ensure_loaded()
        raw = data.get(slug)
        return _from_dict(raw) if raw is not None else None

    async def async_list(self) -> list[InstallRecord]:
        data = await self._ensure_loaded()
        return [_from_dict(v) for v in data.values()]

    async def async_remove(self, slug: str) -> InstallRecord | None:
        async with self._mutex:
            data = await self._ensure_loaded()
            raw = data.pop(slug, None)
            if raw is None:
                return None
            await self._save()
            _LOGGER.info("Removed recipe install record: %s", slug)
            return _from_dict(raw)


_HASS_DATA_KEY = "_selora_ai_install_store_v2"


def get_install_store(hass: HomeAssistant) -> InstallStore:
    """Singleton accessor — there's only ever one store per HA instance."""
    domain_data = hass.data.setdefault("selora_ai", {})
    store = domain_data.get(_HASS_DATA_KEY)
    if store is None:
        store = InstallStore(hass)
        domain_data[_HASS_DATA_KEY] = store
    return store
