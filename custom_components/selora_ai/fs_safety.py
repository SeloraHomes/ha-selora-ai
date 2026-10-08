"""Writing files under the config folder without following anyone's links.

Shared by the tools that let an MCP client change files (``config_yaml``,
``config_files``). Each rule here closed a way a write reached somewhere it
should not, or lost someone else's save:

* A NEW file is created exclusive and not following links, so a planted
  symlink at the name fails instead of being written through.
* A replacement goes to a unique ``mkstemp`` file first and is swapped in only
  if the target still holds the bytes the edit was derived from — read with
  ``newline=""``, since universal-newline reading turns CRLF into LF and the
  comparison would no longer be about what is on disk.
* "No file" is ``None``, never ``""``: an empty file is a file, and undoing an
  edit must restore it rather than delete it.
* A backup folder must be real directories at every level.
* A backup is read back only by a name listed among that file's own backups.
"""

from __future__ import annotations

import contextlib
from datetime import UTC, datetime
import os
from pathlib import Path
import re
import tempfile
from typing import Any, Final
from urllib.parse import quote

from .helpers import sanitize_untrusted_text

PRIVATE_FILE: Final = 0o600
PRIVATE_DIR: Final = 0o700


# A write keeps only a file's newest backups, and may prune one while it is read.
PRUNED: Final = "That backup of {rel} was just pruned by a newer one; list its backups again."


class UnsafePathError(Exception):
    """A path this module refuses to write through."""


def create_exclusive(path: Path, text: str, mode: int = PRIVATE_FILE) -> None:
    """Write ``text`` to a NEW file created with ``mode`` — never wider first."""
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags, mode)
    with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
        handle.write(text)
    os.chmod(path, mode)


def read_exact(path: Path) -> str | None:
    """The file's text exactly as stored, or None when there is no file."""
    if not path.exists():
        return None
    with path.open(encoding="utf-8", newline="") as handle:
        return handle.read()


def replace_if_unchanged(
    path: Path, expected: str | None, text: str | None, *, new_mode: int = PRIVATE_FILE
) -> bool:
    """Put ``text`` at ``path`` (None removes it) only if it still holds ``expected``.

    Synchronous, so no coroutine falls between the compare and the replace.
    The new bytes are written to a unique temporary file FIRST, leaving only
    the compare and ``os.replace`` back to back. That narrows the window to a
    few system calls but does not close it: a filesystem has no
    compare-and-swap against writers that take no lock. The file keeps its
    mode; a new one gets ``new_mode``.
    """
    if text is None:
        if read_exact(path) != expected:
            return False
        path.unlink(missing_ok=True)
        return True
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = (path.stat().st_mode & 0o777) if path.exists() else new_mode
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".selora-tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
        os.chmod(tmp_name, mode)
        if read_exact(path) != expected:
            Path(tmp_name).unlink(missing_ok=True)
            return False
        os.replace(tmp_name, path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise
    return True


def _backup_folder(config_dir: Path, backup_dir: str, *, create: bool) -> Path | None:
    """The backup folder, real directories at every level; None if not there.

    A planted symlink would take a copy (and the chmod) wherever it points, or
    serve a read from there.
    """
    folder = config_dir
    for part in Path(backup_dir).parts:
        folder = folder / part
        # Created if missing — tolerating another write creating it at the
        # same moment — and THEN checked, so whoever made it, it is a real
        # directory before anything is written into it.
        if not folder.exists() and not folder.is_symlink():
            if not create:
                return None
            with contextlib.suppress(FileExistsError):
                folder.mkdir(mode=PRIVATE_DIR)
        if folder.is_symlink() or not folder.is_dir():
            raise UnsafePathError(
                f"{backup_dir} is not a real folder (a symlink or a file); no backup "
                "is written or read through it."
            )
        if create:
            os.chmod(folder, PRIVATE_DIR)
    return folder


def _backups_of(folder: Path, rel: str) -> list[tuple[str, Path]]:
    """``(stamp, path)`` for each backup of ``rel``, oldest first.

    Matched exactly, not by glob: ``x.*.bak`` also matches ``x.css``'s backups,
    and pruning one file's history would delete the other's.
    """
    pattern = re.compile(re.escape(quote(rel, safe="")) + r"\.(\d{8}T\d{12}Z)\.bak")
    found = [
        (match.group(1), child)
        for child in folder.iterdir()
        if (match := pattern.fullmatch(child.name)) and not child.is_symlink()
    ]
    return sorted(found)


def backup(config_dir: Path, backup_dir: str, rel: str, text: str, kept: int) -> str:
    """Copy ``text`` into ``backup_dir`` owner-only; return its relative path.

    Every level of the backup folder is created here or must be a real
    directory. Only the newest ``kept`` backups of ``rel`` are kept.
    """
    folder = _backup_folder(config_dir, backup_dir, create=True)
    if folder is None:  # create=True makes it or raises
        raise UnsafePathError(f"{backup_dir} could not be made.")
    # Percent-encoding is injective; replacing "/" was not ("a__b" and "a/b"
    # shared one history, and pruning one deleted the other's backups).
    stem = quote(rel, safe="")
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    target = folder / f"{stem}.{stamp}.bak"
    create_exclusive(target, text)
    for _, old in _backups_of(folder, rel)[:-kept]:
        old.unlink(missing_ok=True)
    return f"{backup_dir}/{target.name}"


def list_backups(config_dir: Path, backup_dir: str, rel: str) -> list[dict[str, Any]]:
    """The backups of ``rel``, newest first: what ``read_backup`` takes, and when."""
    folder = _backup_folder(config_dir, backup_dir, create=False)
    if folder is None:
        return []
    listed = []
    for stamp, path in reversed(_backups_of(folder, rel)):
        try:
            size = path.stat().st_size
        except FileNotFoundError:  # pruned by a write since the folder was read
            continue
        saved_at = datetime.strptime(stamp, "%Y%m%dT%H%M%S%fZ").replace(tzinfo=UTC)
        listed.append(
            {"backup": f"{backup_dir}/{path.name}", "saved_at": saved_at.isoformat(), "size": size}
        )
    return listed


def backup_path(config_dir: Path, backup_dir: str, rel: str, backup_ref: str) -> Path:
    """The file ``backup_ref`` names, if it is one of ``rel``'s own backups.

    Only a name ``list_backups`` would return: another file's backup, or any
    other path, is refused, so a backup reference reaches nothing else.
    """
    folder = _backup_folder(config_dir, backup_dir, create=False)
    name = str(backup_ref or "").strip().removeprefix(f"{backup_dir}/")
    found = _backups_of(folder, rel) if folder is not None else []
    match = next((path for _, path in found if path.name == name), None)
    if match is None:
        raise UnsafePathError(
            f"{sanitize_untrusted_text(backup_ref, 120)} is not a backup of {rel}; list its backups for the names."
        )
    return match


def read_backup(config_dir: Path, backup_dir: str, rel: str, backup_ref: str) -> str:
    """The text of one of ``rel``'s backups, exactly as stored."""
    path = backup_path(config_dir, backup_dir, rel, backup_ref)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except FileNotFoundError as exc:
        raise UnsafePathError(PRUNED.format(rel=rel)) from exc
    with os.fdopen(fd, encoding="utf-8", newline="") as handle:
        return handle.read()
