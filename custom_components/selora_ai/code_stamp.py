"""Detects code-on-disk vs code-in-memory skew.

Replacing the integration's files (a HACS update, ``just deploy``) does not
replace what the running process executes: the modules stay in ``sys.modules``,
so even a config-entry reload runs against the *old* module objects — including
the ``@websocket_command`` schemas the panel talks to. The panel bundle has no
such stickiness: it is served straight off disk (no immutable cache headers), so
a page refresh alone hands the browser the newly deployed JS. The browser then
calls last-deploy's backend and gets ``extra keys not allowed`` for a key the
shipped schema declares. Only a full Home Assistant restart clears that.

``LOADED_PYTHON_SIGNATURE`` is computed at import — i.e. from the deploy this
process actually loaded — so any difference from the live signature means the
modules in memory are not the code on disk.

The signature hashes paths and contents rather than comparing mtimes. A newest
mtime is not an identity of the tree: ``just deploy`` uses ``rsync -az``, which
preserves each source file's own mtime, so a deploy routinely lands files
*older* than what's already there; ``--delete`` removes files, which no
timestamp reflects; an edit to any file that isn't the newest leaves a maximum
untouched; and second-resolution truncation hides sub-second edits. Every one of
those would report "nothing to restart" while stale modules keep running.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Final

_COMPONENT_DIR: Final[Path] = Path(__file__).parent
_PANEL_BUILD_FILE: Final[Path] = _COMPONENT_DIR / "frontend" / "panel.build.json"

# `frontend` holds the JS sources and (locally) node_modules — thousands of
# files, no integration Python. `__pycache__` holds bytecode compiled from the
# sources we already hash.
_SKIP_DIRS: Final[frozenset[str]] = frozenset({"frontend", "__pycache__"})

_SIGNATURE_CHARS: Final[int] = 16


def python_signature(root: Path | None = None) -> str:
    """Return a content signature over the component's Python sources.

    Covers every ``*.py`` path and its bytes, so an edit, an addition, a
    deletion, or a rename all change the result. Blocking (reads ~100 files,
    a few ms) — call from an executor.
    """
    base = _COMPONENT_DIR if root is None else root
    digest = hashlib.sha256()
    for current, dirs, files in os.walk(base):
        dirs[:] = sorted(name for name in dirs if name not in _SKIP_DIRS)
        for name in sorted(files):
            if not name.endswith(".py"):
                continue
            path = Path(current) / name
            digest.update(str(path.relative_to(base)).encode())
            digest.update(b"\0")
            try:
                digest.update(path.read_bytes())
            except OSError:
                # Unreadable or vanished mid-deploy: distinct from both "absent"
                # and any real content, so the tree still hashes deterministically
                # and a half-written deploy reads as "not what we loaded".
                digest.update(b"<unreadable>")
            digest.update(b"\0")
    return digest.hexdigest()[:_SIGNATURE_CHARS]


def panel_build_id() -> str:
    """Return the build id of the panel bundle on disk ("" if unavailable).

    Written by ``frontend/build.js`` beside the bundle. mtime would not do: the
    bundle is served without immutable cache headers, so a browser can be handed
    fresh bytes under the URL's older cache-buster.
    """
    try:
        payload = json.loads(_PANEL_BUILD_FILE.read_text())
    except (OSError, ValueError):
        return ""
    build = payload.get("build")
    return build if isinstance(build, str) else ""


# Computed at import time, from the files this process loaded its Python from.
LOADED_PYTHON_SIGNATURE: Final[str] = python_signature()
