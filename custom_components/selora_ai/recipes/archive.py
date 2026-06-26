"""Recipe archive intake: fetch from URL or accept a local upload,
extract safely, validate the manifest, stage the bundle directory under
``<config>/selora_ai_recipes/<slug>/``.

The archive path is separate from the pipeline proper — once a bundle
is on disk under the slug-named directory, the pipeline doesn't care
how it got there (Connect, deploy, URL fetch, upload, manual unzip all
land at the same place). This module just owns the "get bytes to a
trusted directory" step.

Security posture:

- Size caps on the wire and after extraction (``RECIPE_ARCHIVE_MAX_*``).
- ``tar.extractall(filter="data")`` plus an explicit pre-pass that
  rejects absolute paths, ``..`` traversal, symlinks, and zip-bomb
  expansion ratios.
- Bundle is staged into a temporary directory and only swapped into
  the user-facing ``selora_ai_recipes/<slug>/`` after the manifest
  parses cleanly — half-extracted bundles never become visible.
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
from pathlib import Path
import shutil
import tarfile
from typing import TYPE_CHECKING
from urllib.parse import urlparse
import zipfile

import aiohttp
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    RECIPE_ARCHIVE_MAX_BYTES,
    RECIPE_ARCHIVE_MAX_EXTRACTED_BYTES,
    RECIPE_ARCHIVE_MAX_FILES,
    RECIPE_FETCH_TIMEOUT_SECONDS,
)
from .loader import bundles_dir
from .manifest import ManifestError, load_manifest

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)


class ArchiveError(Exception):
    """One reason an archive couldn't be ingested. Caller surfaces the
    message verbatim to the user; keep them single-sentence and
    actionable.
    """


@dataclass(frozen=True, slots=True)
class StagedBundle:
    """What the intake step returns once a bundle is safely on disk.

    Attributes:
        slug: The slug pulled from the manifest. Same as the destination
            directory name.
        path: Absolute path of the user-facing
            ``<config>/selora_ai_recipes/<slug>/`` directory.
        version: From the manifest, useful for the wizard's confirmation.
        title: From the manifest, ditto.
    """

    slug: str
    path: Path
    version: str
    title: str


# ── URL validation + fetch ──────────────────────────────────────────


_ALLOWED_SUFFIXES = (".tar.gz", ".tgz", ".zip")


def _validate_url(url: str) -> None:
    """Refuse URLs we can't safely fetch.

    Only enforces scheme + suffix. The user is the one pasting the URL;
    LAN-only http registries are a legitimate dev case so we don't gate
    on TLS. Host validation is delegated to aiohttp.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ArchiveError(f"Unsupported URL scheme: {parsed.scheme or '(none)'}")
    if not parsed.hostname:
        raise ArchiveError("Recipe URL is missing a host")
    if not parsed.path or not parsed.path.lower().endswith(_ALLOWED_SUFFIXES):
        raise ArchiveError("Recipe URL must point to a .tar.gz, .tgz, or .zip archive")


def _suffix_ok(filename: str) -> bool:
    """True when the file name looks like a recipe archive. Used by the
    upload view to gate before reading the body.
    """
    return filename.lower().endswith(_ALLOWED_SUFFIXES)


async def async_fetch_archive(hass: HomeAssistant, url: str, *, dest_dir: Path) -> Path:
    """Download ``url`` to ``dest_dir`` and return the local archive path.

    Streams the response in chunks with a Content-Length pre-check,
    aborting as soon as the running total exceeds RECIPE_ARCHIVE_MAX_BYTES
    — so a server that lies about or omits Content-Length still gets caught
    before the full body is buffered into memory.
    """
    _validate_url(url)
    await hass.async_add_executor_job(lambda: dest_dir.mkdir(parents=True, exist_ok=True))
    basename = Path(urlparse(url).path).name or "recipe.tar.gz"
    target = dest_dir / basename

    session = async_get_clientsession(hass)
    try:
        async with session.get(url, timeout=RECIPE_FETCH_TIMEOUT_SECONDS) as response:
            if response.status != 200:
                raise ArchiveError(f"Recipe URL returned HTTP {response.status}: {url}")
            declared = response.headers.get("Content-Length")
            if (
                declared is not None
                and declared.isdigit()
                and int(declared) > RECIPE_ARCHIVE_MAX_BYTES
            ):
                raise ArchiveError(
                    f"Recipe archive too large: {declared} bytes (max {RECIPE_ARCHIVE_MAX_BYTES})"
                )
            # Stream and enforce the cap as we go: a missing/understated
            # Content-Length must not let an oversized body materialize.
            # Memory stays bounded at the cap plus one chunk.
            body = bytearray()
            async for chunk in response.content.iter_chunked(64 * 1024):
                body += chunk
                if len(body) > RECIPE_ARCHIVE_MAX_BYTES:
                    raise ArchiveError(
                        f"Recipe archive exceeded {RECIPE_ARCHIVE_MAX_BYTES} bytes; "
                        "download aborted"
                    )
            await hass.async_add_executor_job(target.write_bytes, bytes(body))
    except aiohttp.ClientConnectorError as exc:
        raise ArchiveError(
            f"Could not reach {url}. If Home Assistant is running in a "
            "container, 'localhost' refers to the container itself — use "
            f"the host's LAN IP instead. ({exc})"
        ) from exc
    except aiohttp.ClientError as exc:
        raise ArchiveError(f"Failed to fetch recipe from {url}: {exc}") from exc
    except TimeoutError as exc:
        raise ArchiveError(
            f"Timed out fetching recipe from {url} after {RECIPE_FETCH_TIMEOUT_SECONDS}s"
        ) from exc
    return target


# ── Safe extraction ─────────────────────────────────────────────────


def _detect_format(archive_path: Path) -> str:
    """Decide tar vs zip from magic bytes (not extension)."""
    with archive_path.open("rb") as fh:
        head = fh.read(4)
    if head[:2] == b"\x1f\x8b":
        return "tar"
    if head[:4] in (b"PK\x03\x04", b"PK\x05\x06"):
        return "zip"
    raise ArchiveError(
        "Recipe archive is neither a gzipped tarball nor a ZIP (unrecognised magic bytes)."
    )


def _safe_extract_tar(archive_path: Path, dest: Path) -> None:
    """Extract a .tar.gz with zip-slip + zip-bomb guards.

    Refuses absolute paths, ``..`` traversal, sym/hard-links, and any
    archive that exceeds the file count or total extracted size caps.
    """
    dest_resolved = dest.resolve()
    with tarfile.open(archive_path, mode="r:gz") as tar:
        members = tar.getmembers()
        if len(members) > RECIPE_ARCHIVE_MAX_FILES:
            raise ArchiveError(
                f"Recipe archive has too many files: {len(members)} "
                f"(max {RECIPE_ARCHIVE_MAX_FILES})"
            )
        total = 0
        for member in members:
            if member.islnk() or member.issym():
                raise ArchiveError(f"Recipe archive contains a link: {member.name}")
            if member.name.startswith("/") or ".." in Path(member.name).parts:
                raise ArchiveError(f"Unsafe path in archive: {member.name}")
            target = (dest / member.name).resolve()
            if not str(target).startswith(str(dest_resolved)):
                raise ArchiveError(f"Path escapes dest: {member.name}")
            total += member.size
            if total > RECIPE_ARCHIVE_MAX_EXTRACTED_BYTES:
                raise ArchiveError(
                    f"Recipe archive too large extracted: {total} bytes "
                    f"(max {RECIPE_ARCHIVE_MAX_EXTRACTED_BYTES})"
                )
        tar.extractall(dest, filter="data")


def _safe_extract_zip(archive_path: Path, dest: Path) -> None:
    """Same guards as the tar path, adapted to ZIP semantics.

    ZIP entries don't carry a "link" type flag the way tar does, so we
    inspect the external_attr's POSIX mode bits for symlinks.
    """
    dest_resolved = dest.resolve()
    with zipfile.ZipFile(archive_path, mode="r") as zf:
        infos = zf.infolist()
        if len(infos) > RECIPE_ARCHIVE_MAX_FILES:
            raise ArchiveError(
                f"Recipe archive has too many files: {len(infos)} (max {RECIPE_ARCHIVE_MAX_FILES})"
            )
        total = 0
        for info in infos:
            name = info.filename
            if name.startswith("/") or name.startswith("\\") or ".." in Path(name).parts:
                raise ArchiveError(f"Unsafe path in archive: {name}")
            target = (dest / name).resolve()
            if not str(target).startswith(str(dest_resolved)):
                raise ArchiveError(f"Path escapes dest: {name}")
            mode = info.external_attr >> 16
            if mode and (mode & 0o170000) == 0o120000:
                raise ArchiveError(f"Recipe archive contains a symlink: {name}")
            total += info.file_size
            if total > RECIPE_ARCHIVE_MAX_EXTRACTED_BYTES:
                raise ArchiveError(
                    f"Recipe archive too large extracted: {total} bytes "
                    f"(max {RECIPE_ARCHIVE_MAX_EXTRACTED_BYTES})"
                )
        zf.extractall(dest)


def _safe_extract(archive_path: Path, dest: Path) -> None:
    """Dispatch to the right extractor by magic-byte detection. Wraps
    corrupt-archive errors so callers can present a clean message.
    """
    dest.mkdir(parents=True, exist_ok=True)
    fmt = _detect_format(archive_path)
    try:
        if fmt == "tar":
            _safe_extract_tar(archive_path, dest)
        else:
            _safe_extract_zip(archive_path, dest)
    except ArchiveError:
        raise
    except (tarfile.ReadError, zipfile.BadZipFile, EOFError) as exc:
        raise ArchiveError(f"Recipe archive is corrupt or not a valid {fmt}: {exc}") from exc


def _find_bundle_root(extracted_dir: Path) -> Path:
    """Locate the directory containing ``manifest.yaml``.

    Archives commonly wrap their contents in a top-level slug
    directory (``leak-lockdown/manifest.yaml``). We accept either
    layout: manifest at the top, or one directory below.
    """
    if (extracted_dir / "manifest.yaml").is_file() or (extracted_dir / "manifest.yml").is_file():
        return extracted_dir
    children = [p for p in extracted_dir.iterdir() if p.is_dir()]
    if len(children) == 1:
        child = children[0]
        if (child / "manifest.yaml").is_file() or (child / "manifest.yml").is_file():
            return child
    raise ArchiveError(
        "Recipe archive has no manifest.yaml at the top level (or in a single top-level directory)."
    )


# ── Stage a downloaded/uploaded archive into the bundle dir ────────


def _stage_archive(hass: HomeAssistant, archive_path: Path) -> StagedBundle:
    """Sync helper: extract → validate manifest → move into place.

    Runs entirely inside an executor hop (the caller wraps this whole
    function in ``async_add_executor_job``) so the YAML parse and the
    filesystem rename don't block the event loop.

    Steps:

    1. Extract to a sibling ``_extract_<stem>/`` directory.
    2. Find the bundle root (where manifest.yaml lives).
    3. Parse the manifest — this is the gate. A bad manifest aborts
       before the user-facing directory is touched.
    4. ``shutil.move`` the validated bundle to its final
       ``selora_ai_recipes/<slug>/`` location (replacing any prior
       version).
    5. Clean up the staging directory.
    """
    staging = archive_path.parent / f"_extract_{archive_path.stem}"
    if staging.exists():
        shutil.rmtree(staging)
    try:
        _safe_extract(archive_path, staging)
        try:
            root = _find_bundle_root(staging)
            manifest = load_manifest(root)
        except (ManifestError, ArchiveError) as exc:
            raise ArchiveError(str(exc)) from exc

        final_dir = bundles_dir(hass) / manifest.slug
        final_dir.parent.mkdir(parents=True, exist_ok=True)
        if final_dir.exists():
            shutil.rmtree(final_dir)
        shutil.move(str(root), str(final_dir))
        return StagedBundle(
            slug=manifest.slug,
            path=final_dir,
            version=manifest.version,
            title=manifest.title,
        )
    finally:
        shutil.rmtree(staging, ignore_errors=True)


async def async_stage_archive_file(hass: HomeAssistant, archive_path: Path) -> StagedBundle:
    """Async wrapper around :func:`_stage_archive` for the HTTP upload
    view + the URL install WS command.
    """
    return await hass.async_add_executor_job(_stage_archive, hass, archive_path)


# ── URL → staged bundle ─────────────────────────────────────────────


async def async_install_from_url(hass: HomeAssistant, url: str) -> StagedBundle:
    """Fetch + stage in one call. The downloaded archive is deleted
    after staging — only the extracted bundle directory stays on disk.
    """
    download_dir = bundles_dir(hass) / "_downloads"
    archive_path = await async_fetch_archive(hass, url, dest_dir=download_dir)
    try:
        return await async_stage_archive_file(hass, archive_path)
    finally:
        try:
            archive_path.unlink(missing_ok=True)
        except OSError:
            _LOGGER.debug("Could not remove downloaded archive %s", archive_path)
