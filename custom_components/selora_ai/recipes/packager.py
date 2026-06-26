"""Disk-side install: write the rendered package, manage configuration.yaml's
packages include, trigger HA reload.

The package file lives at::

    <config>/packages/selora_ai/<slug>.yaml

HA's package mechanism (https://www.home-assistant.io/docs/configuration/packages/)
expects ``configuration.yaml`` to declare the packages directory via
``homeassistant.packages: !include_dir_named packages``. We add that
include on first write, preserving everything else in the file, and
keep a one-off backup so the homeowner can roll back without
spelunking.
"""

from __future__ import annotations

import logging
from pathlib import Path
import re
import shutil
from typing import TYPE_CHECKING

from .const import (
    CONFIGURATION_BACKUP_SUFFIX,
    CONFIGURATION_FILENAME,
    PACKAGE_DIR_NAME,
    PACKAGE_FILE_SUFFIX,
    PACKAGE_NAMESPACE,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)


class PackagerError(Exception):
    """Something went wrong writing the package or updating
    configuration.yaml. The pipeline surfaces this as the "Install
    package" stage's failure.
    """


# ── Path helpers ────────────────────────────────────────────────────


def packages_root(hass: HomeAssistant) -> Path:
    """Return ``<config>/packages``."""
    return Path(hass.config.config_dir) / PACKAGE_DIR_NAME


def selora_packages_dir(hass: HomeAssistant) -> Path:
    """Return ``<config>/packages/selora_ai/`` — the namespace we own."""
    return packages_root(hass) / PACKAGE_NAMESPACE


def _slug_to_filename(slug: str) -> str:
    """Convert a recipe slug to a Home-Assistant-safe package basename.

    HA treats the filename (minus .yaml) as the package's logical
    name and requires it to be a valid slug — ``[a-z0-9_]`` only.
    The manifest loader accepts slugs of alphanumerics, ``-`` and ``_``
    (so uppercase passes), so we normalise hyphens to underscores AND
    lowercase here — otherwise a slug like ``My-Recipe`` would write
    ``My_Recipe.yaml`` and HA rejects the package name on reload after
    the file is already on disk. The in-memory slug used by the wizard /
    WS layer is unchanged.
    """
    return slug.replace("-", "_").lower()


def package_path(hass: HomeAssistant, slug: str) -> Path:
    """Where the rendered file for ``slug`` lands."""
    return selora_packages_dir(hass) / f"{_slug_to_filename(slug)}{PACKAGE_FILE_SUFFIX}"


def configuration_path(hass: HomeAssistant) -> Path:
    return Path(hass.config.config_dir) / CONFIGURATION_FILENAME


# ── configuration.yaml include ──────────────────────────────────────


# Matches an existing ``packages:`` key under ``homeassistant:`` with
# any of HA's package-include forms. We use this to detect whether the
# user (or a prior install) already wired up packages — in which case
# we leave configuration.yaml alone and trust whatever's already there.
_PACKAGES_KEY_RE = re.compile(
    r"^(?P<indent>[ \t]*)packages\s*:",
    re.MULTILINE,
)
_HOMEASSISTANT_HEADER_RE = re.compile(
    r"^homeassistant\s*:\s*$",
    re.MULTILINE,
)
# Any top-level ``homeassistant:`` key, whatever follows it (a block header,
# an inline value, or an ``!include``). Used to tell "no homeassistant block,
# safe to prepend" apart from "block exists in a form we must not duplicate".
_HOMEASSISTANT_KEY_RE = re.compile(
    r"^homeassistant[ \t]*:",
    re.MULTILINE,
)
# ``homeassistant: !include <file>`` — the homeassistant config lives in an
# included file, so the packages key has to go there, not into a second
# top-level block in configuration.yaml.
_HOMEASSISTANT_INLINE_INCLUDE_RE = re.compile(
    r"^homeassistant[ \t]*:[ \t]*!include[ \t]+(?P<file>\S+)[ \t]*$",
    re.MULTILINE,
)
_INCLUDE_DIR_NAMED_DIRECTIVE = "!include_dir_named packages"


def _has_packages_include(configuration_text: str) -> bool:
    """True if configuration.yaml already declares any ``packages:`` key
    under ``homeassistant:``. We don't try to parse the directive — any
    existing form is good enough.
    """
    return bool(_PACKAGES_KEY_RE.search(configuration_text))


def _ensure_packages_include(text: str) -> str:
    """Add a ``packages: !include_dir_named packages`` line under the
    ``homeassistant:`` block. Returns the modified text.

    Handles three input shapes:

    - no ``homeassistant:`` block at all → inject one at the top
    - ``homeassistant:`` block exists but no ``packages:`` key →
      append the line indented under it
    - already has ``packages:`` key → return text unchanged

    Custom YAML parsing rather than safe_load/safe_dump round-trip
    because configuration.yaml is full of ``!include`` and
    ``!secret`` tags that ruamel/PyYAML lose without a custom resolver
    chain. Working with the text directly keeps comments, ordering, and
    tags exactly as the user authored them.
    """
    if _has_packages_include(text):
        return text

    ha_match = _HOMEASSISTANT_HEADER_RE.search(text)
    inject_line = f"  packages: {_INCLUDE_DIR_NAMED_DIRECTIVE}"

    if ha_match is None:
        if _HOMEASSISTANT_KEY_RE.search(text) is not None:
            # A ``homeassistant:`` key exists but not as a bare block header
            # (e.g. an inline ``!include``/``!include_dir_*`` or inline value).
            # Prepending another top-level block would create a duplicate key
            # and break the next core-config reload. The filesystem-aware
            # ``ensure_packages_include`` handles the include case; anything
            # else is surfaced here rather than silently corrupting the file.
            raise PackagerError(
                "configuration.yaml defines `homeassistant:` in a form Selora "
                "can't safely edit; add "
                f"`packages: {_INCLUDE_DIR_NAMED_DIRECTIVE}` to your "
                "homeassistant config manually, then retry."
            )
        # No homeassistant: block. Prepend one.
        prefix = "homeassistant:\n" + inject_line + "\n\n"
        if text and not text.endswith("\n"):
            text += "\n"
        return prefix + text

    # Append the packages line right after the ``homeassistant:`` header.
    # We insert at the end of the header line and let the existing block
    # continue beneath.
    insert_at = ha_match.end()
    return text[:insert_at] + "\n" + inject_line + text[insert_at:]


def _backup_path(config_path: Path) -> Path:
    """Choose a one-off backup filename: ``configuration.yaml.selora-ai.backup``.

    Single file (no per-run timestamps) so repeat installs don't
    pollute the config dir. The first install creates the backup; later
    installs are idempotent (configuration.yaml already has the include).
    """
    return config_path.with_suffix(config_path.suffix + CONFIGURATION_BACKUP_SUFFIX)


def ensure_packages_include(hass: HomeAssistant) -> bool:
    """Idempotently make sure configuration.yaml declares the packages
    directory. Returns True if a change was made, False if it was
    already in place.

    Synchronous filesystem I/O — caller wraps in async_add_executor_job.
    """
    config_path = configuration_path(hass)
    if not config_path.is_file():
        # No configuration.yaml at all (very fresh HA) — create one with
        # just our include. The user can layer their own keys on top.
        config_path.write_text(
            f"homeassistant:\n  packages: {_INCLUDE_DIR_NAMED_DIRECTIVE}\n",
            encoding="utf-8",
        )
        _LOGGER.info("Created %s with packages include", config_path)
        return True

    try:
        original = config_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise PackagerError(f"could not read {config_path}: {exc}") from exc

    if _has_packages_include(original):
        return False

    # ``homeassistant: !include <file>`` → the homeassistant config (and so
    # the packages key) lives in the included file. Editing configuration.yaml
    # here would prepend a duplicate ``homeassistant:`` block and break the
    # reload; route the change into the included file instead.
    inline = _HOMEASSISTANT_INLINE_INCLUDE_RE.search(original)
    if inline is not None:
        return _ensure_packages_in_included_file(config_path, inline.group("file"))

    # First touch — back up before editing. Single backup file: if it
    # already exists, leave it (it captures the homeowner's pre-Selora
    # state; later edits aren't ours to second-guess).
    backup = _backup_path(config_path)
    if not backup.exists():
        try:
            shutil.copy2(config_path, backup)
        except OSError as exc:
            raise PackagerError(f"could not write backup {backup}: {exc}") from exc

    # Raises PackagerError if a non-header homeassistant key is present in a
    # form we can't safely edit (surfaced to the installer, not corrupted).
    updated = _ensure_packages_include(original)
    try:
        config_path.write_text(updated, encoding="utf-8")
    except OSError as exc:
        raise PackagerError(f"could not write {config_path}: {exc}") from exc
    _LOGGER.info("Added packages include to %s (backup at %s)", config_path, backup)
    return True


def _ensure_packages_in_included_file(config_path: Path, include_file: str) -> bool:
    """Add the packages include to the file that ``homeassistant:`` includes.

    Used when configuration.yaml has ``homeassistant: !include <file>``: the
    homeassistant mapping lives in ``<file>``, so the packages key belongs
    there (at top level, since that file *is* the homeassistant block's body).
    Idempotent — returns False when the included file already declares it.
    """
    # The regex captures the raw token, which YAML allows to be quoted
    # (``!include "homeassistant.yaml"``). Strip a matched surrounding quote
    # pair so we edit the real file instead of creating one literally named
    # ``"homeassistant.yaml"`` — which would leave packages unloaded.
    include_file = include_file.strip()
    if len(include_file) >= 2 and include_file[0] in "\"'" and include_file[-1] == include_file[0]:
        include_file = include_file[1:-1]
    included = (config_path.parent / include_file).resolve()
    try:
        text = included.read_text(encoding="utf-8") if included.is_file() else ""
    except OSError as exc:
        raise PackagerError(f"could not read included file {included}: {exc}") from exc

    if _has_packages_include(text):
        return False

    if included.is_file():
        backup = _backup_path(included)
        if not backup.exists():
            try:
                shutil.copy2(included, backup)
            except OSError as exc:
                raise PackagerError(f"could not write backup {backup}: {exc}") from exc

    # Top-level key in the included file (no indent — it's already the body
    # of the homeassistant: block). Append so leading comments/markers stay put.
    sep = "" if (not text or text.endswith("\n")) else "\n"
    new_text = f"{text}{sep}packages: {_INCLUDE_DIR_NAMED_DIRECTIVE}\n"
    try:
        included.parent.mkdir(parents=True, exist_ok=True)
        included.write_text(new_text, encoding="utf-8")
    except OSError as exc:
        raise PackagerError(f"could not write {included}: {exc}") from exc
    _LOGGER.info("Added packages include to %s (homeassistant: !include target)", included)
    return True


# ── Package file write/remove ───────────────────────────────────────


def write_package(hass: HomeAssistant, slug: str, yaml_text: str) -> Path:
    """Write the rendered package to disk. Creates the namespace
    directory if needed. Atomic via write-to-temp + rename so HA's
    file-watcher never sees a half-written package.

    Returns the absolute path of the written file.
    """
    target = package_path(hass, slug)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(target.suffix + ".tmp")
        tmp.write_text(yaml_text, encoding="utf-8")
        tmp.replace(target)
    except OSError as exc:
        raise PackagerError(f"could not write package to {target}: {exc}") from exc
    _LOGGER.info("Wrote recipe package %s", target)
    return target


def update_package_groups(
    hass: HomeAssistant,
    slug: str,
    groups: dict[str, list[str]],
) -> Path:
    """v3 rebind: replace only the ``group:`` section in an installed
    package YAML, leaving automations untouched.

    ``groups`` maps ``group_object_id`` → list of entity_ids. The
    object-ids come from the renderer's ``_group_object_id`` helper
    so callers don't need to know the format. Returns the path of
    the updated file.

    Read-modify-write so user edits to automations (which they
    shouldn't make, but might) survive a rebind. A full re-render
    would clobber them; here we touch only the binding rows.
    """
    import yaml

    target = package_path(hass, slug)
    if not target.is_file():
        raise PackagerError(f"no installed package found for slug {slug!r} at {target}")
    try:
        raw = target.read_text(encoding="utf-8")
    except OSError as exc:
        raise PackagerError(f"could not read {target}: {exc}") from exc

    # Split the header comment off so we round-trip the "DO NOT EDIT"
    # banner without re-deriving it from the manifest. The header is
    # every leading comment line; the YAML body starts at the first
    # non-comment, non-blank line.
    header_lines: list[str] = []
    body_lines = raw.splitlines(keepends=True)
    while body_lines and (body_lines[0].lstrip().startswith("#") or not body_lines[0].strip()):
        header_lines.append(body_lines.pop(0))
    body = "".join(body_lines)
    parsed = yaml.safe_load(body) or {}
    if not isinstance(parsed, dict):
        raise PackagerError(f"package at {target} doesn't parse as a YAML mapping")

    existing_groups = parsed.get("group")
    if not isinstance(existing_groups, dict):
        raise PackagerError(
            f"package at {target} has no group: section to rebind — "
            "only recipes installed with binding_mode=group support rebind"
        )
    for object_id, entities in groups.items():
        if object_id not in existing_groups:
            # Author error or someone hand-edited the file. Don't
            # silently add a new group — caller should know.
            raise PackagerError(
                f"group {object_id!r} not present in package {target}; rebind aborted"
            )
        group_body = existing_groups[object_id]
        if not isinstance(group_body, dict):
            # Hand-edited package: the group body was blanked or replaced
            # with a scalar. Surface a PackagerError (which the rebind WS
            # handler renders gracefully) rather than crashing on a TypeError.
            raise PackagerError(
                f"group {object_id!r} in package {target} is not a mapping; rebind aborted"
            )
        group_body["entities"] = list(entities)
    parsed["group"] = existing_groups

    new_body = yaml.safe_dump(
        parsed,
        default_flow_style=False,
        sort_keys=True,
        allow_unicode=True,
        indent=2,
    )
    new_text = "".join(header_lines) + new_body
    try:
        tmp = target.with_suffix(target.suffix + ".tmp")
        tmp.write_text(new_text, encoding="utf-8")
        tmp.replace(target)
    except OSError as exc:
        raise PackagerError(f"could not rewrite {target}: {exc}") from exc
    _LOGGER.info("Rebound recipe package %s (%d group(s))", target, len(groups))
    return target


def remove_package(hass: HomeAssistant, slug: str) -> bool:
    """Delete the package file for ``slug``. Returns True when at least
    one matching file existed and was removed; False otherwise.

    Also cleans up legacy hyphenated filenames left by earlier
    installs (before the slug-to-filename normalisation was added) so
    re-installing a recipe doesn't leave HA logging "invalid slug"
    forever on the stale file.
    """
    removed = False
    candidates = {package_path(hass, slug)}
    if "-" in slug:
        candidates.add(selora_packages_dir(hass) / f"{slug}{PACKAGE_FILE_SUFFIX}")
    for target in candidates:
        if not target.is_file():
            continue
        try:
            target.unlink()
        except OSError as exc:
            raise PackagerError(f"could not remove {target}: {exc}") from exc
        _LOGGER.info("Removed recipe package %s", target)
        removed = True
    return removed


# ── Reload HA ───────────────────────────────────────────────────────


async def async_reload_core_config(hass: HomeAssistant) -> None:
    """Reload the core HA config so package changes take effect.

    Two-step reload because they do different things:

    1. ``homeassistant.reload_core_config`` re-reads configuration.yaml
       and every ``!include_dir_named packages/`` entry. Without this
       a freshly-written package file is still invisible to HA.
    2. Per-domain reloads (or ``homeassistant.reload_all`` as a
       shortcut) restart the YAML-driven domains so they pick up the
       new contributions. ``reload_all`` alone is NOT enough — it
       doesn't re-read packages, so the running engines never see
       the recipe's automations / groups / scripts.

    Failures on a single domain are logged but the chain continues —
    we'd rather have a partial reload than abort with the rest of
    the recipe stuck on the previous state.
    """
    services = hass.services
    # Step 1: re-read package files into HA's config tree. This is the
    # critical step — without it the freshly-written package is never read,
    # so a failure here (invalid package YAML, bad configuration.yaml edit)
    # MUST propagate. Otherwise the install pipeline records the recipe as
    # active even though HA never loaded it. Steps 2+ are best-effort.
    if services.has_service("homeassistant", "reload_core_config"):
        try:
            await hass.services.async_call("homeassistant", "reload_core_config", blocking=True)
        except Exception as exc:  # noqa: BLE001 — re-raised as PackagerError below
            raise PackagerError(f"reload_core_config failed: {exc}") from exc

    # Step 2: bounce every YAML-driven engine so it re-reads from the
    # now-refreshed config. ``reload_all`` covers most of them in one
    # call; we still iterate the per-domain reloads as a fallback for
    # anything reload_all doesn't touch (notably the legacy ``group:``
    # platform on some HA versions).
    if services.has_service("homeassistant", "reload_all"):
        try:
            await hass.services.async_call("homeassistant", "reload_all", blocking=True)
        except Exception as exc:  # noqa: BLE001 — fall through to per-domain
            _LOGGER.warning("reload_all failed: %s", exc)
    for domain in ("group", "automation", "script", "scene", "template"):
        if services.has_service(domain, "reload"):
            try:
                await hass.services.async_call(domain, "reload", blocking=True)
            except Exception as exc:  # noqa: BLE001 — keep reloading other domains
                _LOGGER.warning("%s.reload during recipe reload failed: %s", domain, exc)
