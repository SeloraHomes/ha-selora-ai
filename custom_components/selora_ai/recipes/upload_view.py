"""HTTP view that accepts a multipart upload of a recipe archive.

The URL path covers "paste a public URL"; this endpoint covers locally-
authored or hand-distributed bundles where there's no host to fetch
from. WebSocket can't carry binary uploads in HA, so we accept a
regular aiohttp multipart POST.

Security posture mirrors the URL path:

- Admin-only.
- Hard cap on body size — refused early on Content-Length, then
  re-checked while streaming chunks.
- Filename suffix validated up-front, magic bytes verified by the
  extractor.
- File written to the same staging directory the URL fetch uses, then
  ``async_stage_archive_file`` runs through the executor.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from aiohttp import web
from homeassistant.components.http import HomeAssistantView

from .archive import (
    ArchiveError,
    _suffix_ok,
    async_stage_archive_file,
)
from .const import RECIPE_ARCHIVE_MAX_BYTES, RECIPE_BUNDLE_DIR

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

UPLOAD_PATH = "/api/selora_ai/recipes/upload"


class RecipeUploadView(HomeAssistantView):
    """Accept a multipart upload of a recipe archive and stage it under
    ``<config>/selora_ai_recipes/<slug>/``.
    """

    url = UPLOAD_PATH
    name = "api:selora_ai:recipes_upload"
    requires_auth = True

    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass

    async def post(self, request: web.Request) -> web.Response:
        user = request.get("hass_user")
        if user is None or not getattr(user, "is_admin", False):
            return self.json_message(
                "Selora AI recipe upload requires an administrator account",
                status_code=403,
            )

        declared = request.headers.get("Content-Length")
        if declared is not None and declared.isdigit() and int(declared) > RECIPE_ARCHIVE_MAX_BYTES:
            return self.json_message(
                f"Recipe archive too large: {declared} bytes (max {RECIPE_ARCHIVE_MAX_BYTES})",
                status_code=413,
            )

        try:
            reader = await request.multipart()
        except (ValueError, web.HTTPException) as exc:
            return self.json_message(f"Expected multipart/form-data: {exc}", status_code=400)

        field = await reader.next()
        # Skip non-file form fields silently — many clients send a CSRF
        # field first. We only care about the file part.
        while field is not None and getattr(field, "filename", None) is None:
            field = await reader.next()
        if field is None:
            return self.json_message(
                "No file part in upload (expected a multipart field with a filename)",
                status_code=400,
            )

        filename = Path(field.filename or "").name
        if not filename or not _suffix_ok(filename):
            return self.json_message(
                "Recipe archive must be a .tar.gz, .tgz, or .zip file",
                status_code=400,
            )

        download_dir = Path(self._hass.config.path(RECIPE_BUNDLE_DIR)) / "_uploads"
        try:
            await self._hass.async_add_executor_job(
                lambda: download_dir.mkdir(parents=True, exist_ok=True)
            )
        except OSError as exc:
            _LOGGER.exception("Could not create recipe upload dir")
            return self.json_message(
                f"Could not prepare upload destination: {exc}", status_code=500
            )

        target = download_dir / filename
        total = 0
        try:
            with target.open("wb") as fh:
                while True:
                    chunk = await field.read_chunk()
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > RECIPE_ARCHIVE_MAX_BYTES:
                        fh.close()
                        target.unlink(missing_ok=True)
                        return self.json_message(
                            f"Recipe archive exceeded {RECIPE_ARCHIVE_MAX_BYTES} "
                            "bytes during upload",
                            status_code=413,
                        )
                    fh.write(chunk)
        except OSError as exc:
            target.unlink(missing_ok=True)
            _LOGGER.exception("Failed writing recipe upload to disk")
            return self.json_message(f"Failed to save upload: {exc}", status_code=500)

        try:
            staged = await async_stage_archive_file(self._hass, target)
        except ArchiveError as exc:
            return self.json_message(str(exc), status_code=400)
        finally:
            target.unlink(missing_ok=True)

        return self.json(
            {
                "slug": staged.slug,
                "title": staged.title,
                "version": staged.version,
                "path": str(staged.path),
            }
        )


def async_register_recipe_upload_view(hass: HomeAssistant) -> None:
    """Register the upload view once at setup. Idempotent at HA level —
    re-registering the same view name is a no-op.
    """
    hass.http.register_view(RecipeUploadView(hass))
