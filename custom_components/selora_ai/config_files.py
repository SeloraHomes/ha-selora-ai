"""List, read, write and delete files under a few config subfolders, over MCP.

The files a dashboard or theme is made of that no API stores: images and card
files in ``www/`` (served at ``/local/``), theme files, Jinja macros in
``custom_templates/``, YAML dashboards, and blueprints (read-only — importing
one is ``blueprint/save``'s job). Modelled on ha-mcp's file tools.

* **Only these folders**, and only real ones: a path is plain segments
  (letters, digits, ``.``, ``_``, ``-``; no ``..``, no leading dot), checked
  before it is joined; no segment may be a symlink; the resolved path must
  stay in its folder; ``secrets.yaml`` and ``.storage`` never resolve.
* **Text only.** A file that is not UTF-8 is refused on read; writes are text.
* **A replacement needs ``overwrite: true``**, and the previous bytes go to a
  backup first. A delete backs up too.
* **Code a browser runs (and CSS it applies) needs ``confirmed: true``.** A ``/local/`` path can be
  registered as a dashboard resource without confirmation, because the file
  was put there by HACS or the user. A file this tool writes was put there by
  the agent, so ``.js``, ``.html``, ``.svg`` and the like in ``www/`` are
  confirmed here instead — or writing, then registering, would skip the
  confirmation an external URL needs.
* Configuration YAML is ``config_yaml``'s: ``configuration.yaml`` and packages
  are not reachable here, so its allowlist and checks cannot be bypassed.
"""

from __future__ import annotations

from datetime import UTC, datetime
import os
from pathlib import Path
import re
from typing import TYPE_CHECKING, Any, Final

from . import fs_safety
from .helpers import sanitize_untrusted_text

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

READ_DIRS: Final = ("www", "themes", "custom_templates", "dashboards", "blueprints")
WRITE_DIRS: Final = ("www", "themes", "custom_templates", "dashboards")

# Served from www/ and executed, rendered as a document, or applied as a
# stylesheet by the browser. CSS counts: registered as a `css` resource it
# styles every page, and `url()` lookups can carry what it reads off the page.
_BROWSER_CODE: Final = frozenset({".js", ".mjs", ".cjs", ".html", ".htm", ".xhtml", ".svg", ".css"})

_SEGMENT_RE: Final = re.compile(r"[A-Za-z0-9_][A-Za-z0-9._-]*")
_MAX_DEPTH: Final = 8
_MAX_WRITE_BYTES: Final = 1_000_000
_READ_CHUNK_BYTES: Final = 12_000
_MAX_LISTED: Final = 500
_BACKUP_DIR: Final = ".selora_ai/file_backups"
_BACKUPS_KEPT: Final = 5
# Files the frontend and HA read: readable by the web server like any other.
_PUBLIC_FILE: Final = 0o644


class FileToolError(Exception):
    """A request this module refuses, with the reason to report."""


def _check_no_symlinks(config_dir: Path, rel: str) -> None:
    """Refuse a path any level of which is a symlink.

    A link inside the config folder passes a boundary check while aliasing
    something else (secrets.yaml, .storage). Checked when the path is resolved
    and AGAIN inside each file operation, right before the open or replace, so
    a folder swapped for a link in between is caught. That narrows the race to
    a few system calls without closing it — closing it means descriptor-relative
    traversal throughout. It is not a boundary worth that: swapping a folder for
    a link needs write access to the config folder, which already reaches
    configuration.yaml directly.
    """
    current = config_dir
    for part in rel.split("/"):
        current = current / part
        if current.is_symlink():
            raise FileToolError(f"{rel} goes through a symlink; use the real path.")


def _resolve(
    hass: HomeAssistant, raw: str, *, write: bool, folder_ok: bool = False
) -> tuple[Path, str]:
    """``(path, normalized)`` for an allowed path, or FileToolError.

    Only a listing may name a root folder itself: a write to ``www`` while the
    folder does not exist yet would create a FILE there and block every
    ``/local/`` file after it.
    """
    config_dir = Path(hass.config.config_dir)
    rel = str(raw or "").strip().strip("/")
    parts = rel.split("/") if rel else []
    folders = WRITE_DIRS if write else READ_DIRS
    if not parts or parts[0] not in folders:
        raise FileToolError(
            f"Paths must start with one of: {', '.join(folders)}"
            + (" (blueprints are read-only)." if write else ".")
        )
    if len(parts) < 2 and not folder_ok:
        raise FileToolError(f"Name a file inside {parts[0]}/, e.g. {parts[0]}/example.txt.")
    if len(parts) > _MAX_DEPTH or not all(_SEGMENT_RE.fullmatch(p) for p in parts):
        raise FileToolError(
            "A path is plain names — letters, digits, '.', '_' and '-', no '..', none "
            "starting with a dot."
        )
    _check_no_symlinks(config_dir, rel)
    path = config_dir / rel
    resolved = Path(os.path.realpath(path))
    base = Path(os.path.realpath(config_dir / parts[0]))
    if resolved != base and not str(resolved).startswith(str(base) + os.sep):
        raise FileToolError("That path resolves outside its folder.")
    if resolved.name == "secrets.yaml" or ".storage" in resolved.parts:
        raise FileToolError("That file is not one this tool reads or writes.")
    return path, rel


async def async_list(hass: HomeAssistant, folder: str, pattern: str | None) -> dict[str, Any]:
    """Files and folders under ``folder`` (one level), optionally glob-filtered."""

    def _list(path: Path) -> list[dict[str, Any]]:
        _check_no_symlinks(Path(hass.config.config_dir), folder.strip().strip("/"))
        if not path.is_dir():
            raise FileToolError(f"{folder} is not a folder.")
        entries = []
        for child in sorted(path.iterdir()):
            if child.name.startswith(".") or child.is_symlink():
                continue
            if pattern and not child.match(pattern):
                continue
            stat = child.stat()
            entries.append(
                {
                    "name": child.name,
                    "type": "folder" if child.is_dir() else "file",
                    **({} if child.is_dir() else {"size": stat.st_size}),
                    "modified": datetime.fromtimestamp(stat.st_mtime, UTC).isoformat(),
                }
            )
        return entries

    try:
        path, rel = _resolve(hass, folder, write=False, folder_ok=True)
        entries = await hass.async_add_executor_job(_list, path)
    except FileToolError as exc:
        return {"error": str(exc)}
    result: dict[str, Any] = {"folder": rel, "entries": entries[:_MAX_LISTED]}
    if len(entries) > _MAX_LISTED:
        result["omitted"] = len(entries) - _MAX_LISTED
    return result


def _read_chunk(config_dir: Path, path: Path, rel: str, offset: int) -> tuple[str, int, int]:
    """``(text, bytes consumed, file size)`` for one chunk from byte ``offset``.

    Reads only the chunk: decoding the whole file to slice 12 KB made paging
    through a large bundle quadratic. An incremental decoder stops short of a
    character the chunk boundary splits, so the next offset is always on a
    character boundary; one that is not is refused.
    """
    import codecs  # noqa: PLC0415

    _check_no_symlinks(config_dir, rel)
    if path.is_dir():
        raise FileToolError(f"{rel} is a folder; list it instead.")
    if not path.exists():
        raise FileToolError(f"{rel} does not exist.")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    with os.fdopen(os.open(path, flags), "rb") as handle:
        size = os.fstat(handle.fileno()).st_size
        if offset > size:
            raise FileToolError(f"offset is past the end of {rel} ({size} bytes).")
        handle.seek(offset)
        raw = handle.read(_READ_CHUNK_BYTES)
    at_end = offset + len(raw) >= size
    decoder = codecs.getincrementaldecoder("utf-8")()
    text = decoder.decode(raw, final=at_end)
    pending = len(decoder.getstate()[0])
    return text, len(raw) - pending, size


async def async_read(hass: HomeAssistant, file: str, offset: int = 0) -> dict[str, Any]:
    """A text file's content, one chunk at a time, by byte offset."""
    try:
        path, rel = _resolve(hass, file, write=False)
        text, consumed, size = await hass.async_add_executor_job(
            _read_chunk, Path(hass.config.config_dir), path, rel, max(0, int(offset or 0))
        )
    except FileToolError as exc:
        return {"error": str(exc)}
    except UnicodeDecodeError:
        return {
            "error": (
                f"{rel} is not UTF-8 text here — not a text file, or the offset is not on a "
                "character boundary (use the next_offset a previous read returned)."
            )
        }
    start = max(0, int(offset or 0))
    result: dict[str, Any] = {"file": rel, "size": size, "content": text}
    if start + consumed < size:
        result["next_offset"] = start + consumed
    return result


def _needs_confirmation(rel: str) -> bool:
    return rel.startswith("www/") and Path(rel).suffix.lower() in _BROWSER_CODE


async def async_write(
    hass: HomeAssistant,
    file: str,
    content: str,
    *,
    overwrite: bool = False,
    confirmed: bool = False,
) -> dict[str, Any]:
    """Create a text file, or replace one with ``overwrite``, backing it up."""
    from .command_policy_options import resolve_command_policy_options  # noqa: PLC0415

    if not isinstance(content, str):
        return {"error": "content must be text."}
    if len(content.encode("utf-8")) > _MAX_WRITE_BYTES:
        return {"error": f"content is over {_MAX_WRITE_BYTES // 1_000_000} MB."}
    try:
        path, rel = _resolve(hass, file, write=True)
    except FileToolError as exc:
        return {"error": str(exc)}
    if (
        _needs_confirmation(rel)
        and not confirmed
        and resolve_command_policy_options(hass).approval_required
    ):
        return {
            "written": False,
            "requires_confirmation": True,
            "file": rel,
            "reason": (
                "This file is code a browser runs when it is loaded from /local/. Show "
                "the user what it does; once they agree, call again with confirmed=true."
            ),
        }

    config_dir = Path(hass.config.config_dir)

    def _write() -> dict[str, Any]:
        _check_no_symlinks(config_dir, rel)
        if path.is_dir():
            raise FileToolError(f"{rel} is a folder.")
        old = fs_safety.read_exact(path)
        if old is not None and not overwrite:
            raise FileToolError(f"{rel} exists. Pass overwrite=true to replace it.")
        saved = (
            fs_safety.backup(config_dir, _BACKUP_DIR, rel, old, _BACKUPS_KEPT)
            if old is not None
            else None
        )
        if not fs_safety.replace_if_unchanged(path, old, content, new_mode=_PUBLIC_FILE):
            raise FileToolError(f"{rel} changed while it was being written; nothing was written.")
        return {"created": old is None, "backup": saved}

    try:
        outcome = await hass.async_add_executor_job(_write)
    except (FileToolError, fs_safety.UnsafePathError) as exc:
        return {"error": str(exc), "written": False}
    except UnicodeDecodeError:
        return {"error": f"{rel} is not a text file; it cannot be replaced here.", "written": False}
    result: dict[str, Any] = {
        "written": True,
        "file": rel,
        "size": len(content.encode("utf-8")),
        "created": outcome["created"],
    }
    if outcome["backup"]:
        result["backup"] = outcome["backup"]
    if rel.startswith("www/"):
        result["url"] = "/local/" + rel.removeprefix("www/")
    return result


async def async_delete(hass: HomeAssistant, file: str) -> dict[str, Any]:
    """Delete a text file, backing it up first."""
    try:
        path, rel = _resolve(hass, file, write=True)
    except FileToolError as exc:
        return {"error": str(exc)}
    config_dir = Path(hass.config.config_dir)

    def _delete() -> str:
        _check_no_symlinks(config_dir, rel)
        if path.is_dir():
            raise FileToolError(f"{rel} is a folder; only files are deleted here.")
        old = fs_safety.read_exact(path)
        if old is None:
            raise FileToolError(f"{rel} does not exist.")
        saved = fs_safety.backup(config_dir, _BACKUP_DIR, rel, old, _BACKUPS_KEPT)
        if not fs_safety.replace_if_unchanged(path, old, None):
            raise FileToolError(f"{rel} changed while it was being deleted; it was kept.")
        return saved

    try:
        saved = await hass.async_add_executor_job(_delete)
    except (FileToolError, fs_safety.UnsafePathError) as exc:
        return {"error": str(exc)}
    except UnicodeDecodeError:
        return {"error": f"{rel} is not a text file; it cannot be deleted here."}
    return {"deleted": True, "file": sanitize_untrusted_text(rel, 200), "backup": saved}
