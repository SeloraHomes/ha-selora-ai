"""Bundle loader — find recipe directories on disk and load their manifests.

Recipe bundles live under ``<config>/selora_ai_recipes/<slug>/``. Each
directory MUST contain a ``manifest.yaml`` and a ``package/`` subtree
of Jinja templates referenced by the manifest. Authoring is upstream
(Connect, a Git repo, manual unzip into the directory) — this module
only reads.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import logging
from pathlib import Path
import shutil
from typing import TYPE_CHECKING

from .const import RECIPE_BUNDLE_DIR
from .manifest import Manifest, ManifestError, load_manifest

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Bundle:
    """A loaded recipe bundle ready to feed into the pipeline."""

    root: Path
    manifest: Manifest
    # Map of relative path → file contents. We slurp template bodies
    # at load time so the executor hop happens once instead of per-stage.
    templates: dict[str, str] = field(default_factory=dict)


def bundles_dir(hass: HomeAssistant) -> Path:
    """Return the directory all recipe bundles live in for this HA install."""
    return Path(hass.config.path(RECIPE_BUNDLE_DIR))


def _load_one(root: Path) -> Bundle:
    """Synchronous helper — read manifest + slurp every package file.

    Raises :class:`ManifestError` for any structural issue with the
    bundle; callers should surface the message verbatim to the user.
    """
    manifest = load_manifest(root)
    templates: dict[str, str] = {}
    for rel in manifest.package_files:
        path = root / rel
        try:
            templates[rel] = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise ManifestError(f"could not read package template {rel!r}: {exc}") from exc
    return Bundle(root=root, manifest=manifest, templates=templates)


async def async_load_bundle(hass: HomeAssistant, slug: str) -> Bundle:
    """Load a single bundle by slug. Raises :class:`ManifestError` when
    the bundle can't be found or parsed.
    """
    root = bundles_dir(hass) / slug
    return await hass.async_add_executor_job(_load_one, root)


async def async_remove_bundle(hass: HomeAssistant, slug: str) -> bool:
    """Delete a staged bundle directory from disk. Returns True if a
    directory was removed, False if there was nothing to remove.

    Called on uninstall so an uninstalled recipe stops appearing in the
    panel's "On this device" list. Defensive: only removes a direct child
    of the bundles dir (no path traversal via a crafted slug).
    """
    base = bundles_dir(hass)
    target = (base / slug).resolve()

    def _remove() -> bool:
        if target.parent != base.resolve() or not target.is_dir():
            return False
        shutil.rmtree(target)
        _LOGGER.info("Removed recipe bundle directory %s", target)
        return True

    return await hass.async_add_executor_job(_remove)


async def async_list_bundles(hass: HomeAssistant) -> list[Bundle]:
    """Return every parsable bundle on disk. Malformed bundles are
    logged and skipped — one bad bundle shouldn't hide the rest.
    """
    base = bundles_dir(hass)

    def _scan() -> list[Bundle]:
        if not base.is_dir():
            return []
        out: list[Bundle] = []
        for child in sorted(base.iterdir()):
            if not child.is_dir() or child.name.startswith("_"):
                continue
            try:
                out.append(_load_one(child))
            except ManifestError as exc:
                _LOGGER.warning("Skipping malformed recipe bundle at %s: %s", child, exc)
        return out

    return await hass.async_add_executor_job(_scan)
