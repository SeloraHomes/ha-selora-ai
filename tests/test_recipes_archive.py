"""Tests for the recipe archive intake (URL fetch + safe extract).

The archive path is the only way bundles get onto disk other than a
manual file copy or the built-in seed step. Coverage here is what gives
the URL-install and upload UI buttons their guarantee.
"""

from __future__ import annotations

import io
import tarfile
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest

from custom_components.selora_ai.recipes.archive import (
    ArchiveError,
    _safe_extract,
    async_install_from_url,
    async_stage_archive_file,
)


# ── Helpers ─────────────────────────────────────────────────────────


def _build_demo_bundle_bytes(slug: str = "leak-lockdown") -> bytes:
    """Return a .tar.gz containing the shipped leak-lockdown bundle.

    The shipped bundle satisfies the manifest schema and is the exact
    shape an installer / Connect tool would hand us, so it doubles as a
    realistic fixture without us hand-crafting one.
    """
    src = Path(__file__).parent / "recipe_fixtures" / slug
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        tar.add(src, arcname=slug)
    return buf.getvalue()


def _write_archive(tmp_path: Path, name: str, payload: bytes) -> Path:
    archive = tmp_path / name
    archive.write_bytes(payload)
    return archive


# ── Stage from a local archive ─────────────────────────────────────


async def test_stage_archive_file_extracts_and_validates_manifest(
    hass, tmp_path: Path
) -> None:
    hass.config.config_dir = str(tmp_path)
    archive = _write_archive(
        tmp_path, "leak-lockdown.tar.gz", _build_demo_bundle_bytes()
    )

    staged = await async_stage_archive_file(hass, archive)

    assert staged.slug == "leak-lockdown"
    assert staged.version == "2.0.0"
    # Bundle landed under <config>/selora_ai_recipes/<slug>/.
    bundle_root = (
        Path(hass.config.config_dir) / "selora_ai_recipes" / "leak-lockdown"
    )
    assert bundle_root.is_dir()
    assert (bundle_root / "manifest.yaml").is_file()
    assert (
        bundle_root / "package" / "automations" / "engage.yaml.j2"
    ).is_file()


async def test_stage_archive_rejects_path_traversal(hass, tmp_path: Path) -> None:
    """Zip-slip guard: an archive trying to write ``../escape`` must
    fail before any file lands on disk.
    """
    hass.config.config_dir = str(tmp_path)
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        info = tarfile.TarInfo(name="../escape.txt")
        info.size = 5
        tar.addfile(info, io.BytesIO(b"pwned"))
    archive = _write_archive(tmp_path, "evil.tar.gz", buf.getvalue())

    with pytest.raises(ArchiveError, match=r"Unsafe path|\.\."):
        await async_stage_archive_file(hass, archive)
    # Nothing leaked to disk.
    assert not (tmp_path / "escape.txt").exists()


async def test_stage_archive_rejects_corrupt_payload(
    hass, tmp_path: Path
) -> None:
    """Wrong magic bytes → ArchiveError, not an unhandled exception."""
    hass.config.config_dir = str(tmp_path)
    archive = _write_archive(tmp_path, "nope.tar.gz", b"not actually a tarball")
    with pytest.raises(ArchiveError):
        await async_stage_archive_file(hass, archive)


async def test_stage_archive_rejects_missing_manifest(
    hass, tmp_path: Path
) -> None:
    """Archive extracts cleanly but has no manifest.yaml — that's a
    bundle error, surfaced as a clean ArchiveError.
    """
    hass.config.config_dir = str(tmp_path)
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        info = tarfile.TarInfo(name="random/README.md")
        info.size = 5
        tar.addfile(info, io.BytesIO(b"hello"))
    archive = _write_archive(tmp_path, "no-manifest.tar.gz", buf.getvalue())
    with pytest.raises(ArchiveError, match="manifest"):
        await async_stage_archive_file(hass, archive)


async def test_stage_archive_zip_format_works(hass, tmp_path: Path) -> None:
    """Both .tar.gz and .zip are first-class. Verify the zip path
    reaches the same outcome as the tar path.
    """
    hass.config.config_dir = str(tmp_path)
    src = Path(__file__).parent / "recipe_fixtures" / "leak-lockdown"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in src.rglob("*"):
            if path.is_file():
                arcname = f"leak-lockdown/{path.relative_to(src)}"
                zf.write(path, arcname=arcname)
    archive = _write_archive(tmp_path, "leak-lockdown.zip", buf.getvalue())

    staged = await async_stage_archive_file(hass, archive)
    assert staged.slug == "leak-lockdown"


# ── URL install (mocked) ───────────────────────────────────────────


async def test_install_from_url_validates_scheme(
    hass, tmp_path: Path
) -> None:
    hass.config.config_dir = str(tmp_path)
    with pytest.raises(ArchiveError, match="scheme"):
        await async_install_from_url(hass, "file:///etc/passwd")


async def test_install_from_url_validates_suffix(
    hass, tmp_path: Path
) -> None:
    hass.config.config_dir = str(tmp_path)
    with pytest.raises(ArchiveError, match="tar.gz"):
        await async_install_from_url(hass, "https://example.com/recipe.txt")


async def test_install_from_url_happy_path(hass, tmp_path: Path) -> None:
    """Full path: mock the HTTP response to a real tarball; assert the
    bundle is staged under selora_ai_recipes/.
    """
    from pytest_homeassistant_custom_component.test_util.aiohttp import (
        AiohttpClientMocker,
    )

    hass.config.config_dir = str(tmp_path)
    url = "http://localhost:1313/recipes/leak-lockdown.tar.gz"
    payload = _build_demo_bundle_bytes()

    mocker = AiohttpClientMocker()
    mocker.get(url, content=payload)

    session = mocker.create_session(hass.loop)
    try:
        with patch(
            "custom_components.selora_ai.recipes.archive.async_get_clientsession",
            return_value=session,
        ):
            staged = await async_install_from_url(hass, url)
    finally:
        await session.close()

    assert staged.slug == "leak-lockdown"
    bundle_root = (
        Path(hass.config.config_dir) / "selora_ai_recipes" / "leak-lockdown"
    )
    assert (bundle_root / "manifest.yaml").is_file()
    # The downloaded archive itself is cleaned up after staging.
    download_dir = (
        Path(hass.config.config_dir) / "selora_ai_recipes" / "_downloads"
    )
    if download_dir.exists():
        assert not any(download_dir.iterdir())


async def test_install_from_url_refuses_oversized_content_length(
    hass, tmp_path: Path
) -> None:
    """If the server advertises Content-Length over the cap we bail
    early, without trying to read the body.
    """
    from pytest_homeassistant_custom_component.test_util.aiohttp import (
        AiohttpClientMocker,
    )

    hass.config.config_dir = str(tmp_path)
    url = "http://localhost:1313/big.tar.gz"
    mocker = AiohttpClientMocker()
    mocker.get(
        url,
        content=b"x",
        headers={"Content-Length": str(10 * 1024 * 1024 + 1)},
    )

    session = mocker.create_session(hass.loop)
    try:
        with patch(
            "custom_components.selora_ai.recipes.archive.async_get_clientsession",
            return_value=session,
        ):
            with pytest.raises(ArchiveError, match="too large"):
                await async_install_from_url(hass, url)
    finally:
        await session.close()


async def test_install_from_url_aborts_oversized_stream_without_content_length(
    hass, tmp_path: Path
) -> None:
    """When Content-Length is missing/understated, the body must be capped
    while streaming — not buffered whole and checked after the fact.
    """
    from pytest_homeassistant_custom_component.test_util.aiohttp import (
        AiohttpClientMocker,
    )

    hass.config.config_dir = str(tmp_path)
    url = "http://localhost:1313/sneaky.tar.gz"
    mocker = AiohttpClientMocker()
    # Body well over the (patched) cap, but the header lies about its size so
    # the pre-check passes — only the streaming guard can catch this.
    mocker.get(url, content=b"x" * 4096, headers={"Content-Length": "1"})

    session = mocker.create_session(hass.loop)
    try:
        with (
            patch(
                "custom_components.selora_ai.recipes.archive.async_get_clientsession",
                return_value=session,
            ),
            patch(
                "custom_components.selora_ai.recipes.archive.RECIPE_ARCHIVE_MAX_BYTES",
                1024,
            ),
        ):
            with pytest.raises(ArchiveError, match="download aborted"):
                await async_install_from_url(hass, url)
    finally:
        await session.close()


# ── Replacing an existing bundle ───────────────────────────────────


async def test_stage_archive_replaces_existing_bundle(
    hass, tmp_path: Path
) -> None:
    """Re-uploading the same slug replaces the prior copy on disk.
    Lets the user iterate on a recipe without having to manually
    clean up first.
    """
    hass.config.config_dir = str(tmp_path)
    archive = _write_archive(
        tmp_path, "leak.tar.gz", _build_demo_bundle_bytes()
    )

    first = await async_stage_archive_file(hass, archive)
    # Drop a marker into the staged bundle so we can verify it's
    # actually replaced on the second stage.
    marker = first.path / "_marker"
    marker.write_text("first")

    second = await async_stage_archive_file(hass, archive)
    assert second.slug == first.slug
    assert not marker.exists()
