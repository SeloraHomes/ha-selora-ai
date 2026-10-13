"""Revoked Connect support sessions, delivered by Selora OS as a file.

An installer's MCP token carries ``sid``, the Connect support session it was
minted for, and expires at most five minutes after issue. Connect stops
refreshing it when the homeowner ends the session, so an installer cut off
mid-session would keep access until that last token expires. Connect closes
the gap by pushing ``{"sid", "until"}`` to the hub's Config Service, which
writes it here; a token whose ``sid`` is listed is refused like an expired one.

The channel
-----------
``mcp_revoked_sessions.json`` is written by the OS and only read here::

    {"version": 1, "sessions": [{"sid": 123, "until": 1760000000}]}

The OS owns the list — merging a new revoke into it, pruning entries past
``until`` — and renames each version into place. One writer means no merge
race between the host and Core, and the file on disk is what makes a revoke
survive a Core restart: a list held only in memory would hand a revoked agent
its access back for the rest of the window.

``mcp_revocation_applied.json`` is ours, written at setup. It tells the OS this
integration reads the list, so the revoke endpoint can report a hub whose
integration predates it as unable to revoke, rather than accept a revoke that
nothing will honour while Connect believes it landed.

The file is read on demand, when a token carrying a ``sid`` arrives, and at
most once per ``_RECHECK_SECONDS``: a revoke lands on the agent's next request
without a poll running on hubs that never see an installer.

An entry stops counting at ``until``. No token for that session outlives it,
since Connect never mints one past the session's end.
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass, field
import json
import logging
import os
from pathlib import Path
import time
from typing import TYPE_CHECKING
from uuid import uuid4

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

# The channel version we implement; the OS gates on this exact value.
REVOCATION_CHANNEL_VERSION = 1

# Same directory as the Alexa credential: OS-delivered, 0700, beside `.storage`.
REVOCATION_DIR_NAME = ".selora"
REVOCATION_FILE_NAME = "mcp_revoked_sessions.json"
ACK_FILE_NAME = "mcp_revocation_applied.json"

# How stale the cached list may be. A revoke has to land within seconds; a
# busy agent making several requests a second must not cost a read on each.
_RECHECK_SECONDS = 1.0


def revocation_dir(hass: HomeAssistant) -> Path:
    """The directory the OS delivers into."""
    return Path(hass.config.path(REVOCATION_DIR_NAME))


def parse_revocations(raw: bytes) -> dict[int, float] | None:
    """``{sid: until}`` from the file's bytes, or ``None`` if it is not a list.

    An individual malformed entry is skipped rather than failing the file, so
    one bad record cannot lift every other revoke. A later entry for a sid
    already seen keeps the later ``until`` — revoking twice never shortens it.
    """
    try:
        payload = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    sessions = payload.get("sessions")
    if not isinstance(sessions, list):
        return None

    revoked: dict[int, float] = {}
    for item in sessions:
        if not isinstance(item, dict):
            continue
        sid = item.get("sid")
        until = item.get("until")
        # bool is an int in Python and is neither a session id nor a time.
        if isinstance(sid, bool) or not isinstance(sid, int):
            continue
        if isinstance(until, bool) or not isinstance(until, (int, float)):
            continue
        revoked[sid] = max(float(until), revoked.get(sid, float("-inf")))
    return revoked


@dataclass(slots=True)
class _FileState:
    """What the last read found, keyed by the stat it was read under."""

    signature: tuple[int, int, int] | None = None  # (inode, size, mtime_ns)
    revoked: dict[int, float] = field(default_factory=dict)


class MCPSessionRevocations:
    """The revoked-session list, re-read from disk when it changes."""

    def __init__(self, directory: Path) -> None:
        self._path = directory / REVOCATION_FILE_NAME
        self._state = _FileState()
        self._checked_at = float("-inf")
        self._refresh_lock = asyncio.Lock()
        self._warned = False

    def refresh(self) -> None:
        """Re-read the file if it changed. Blocking — use an executor.

        An absent file means nothing is revoked. An unreadable or garbled one
        keeps the last list read: a corrupted file must not reinstate an agent
        the homeowner cut off.
        """
        try:
            stat = self._path.stat()
        except FileNotFoundError:
            self._state = _FileState()
            return
        except OSError as err:
            self._warn(f"{self._path} could not be read ({err})")
            return

        signature = (stat.st_ino, stat.st_size, stat.st_mtime_ns)
        if signature == self._state.signature:
            return
        try:
            raw = self._path.read_bytes()
        except FileNotFoundError:
            self._state = _FileState()
            return
        except OSError as err:
            self._warn(f"{self._path} could not be read ({err})")
            return

        revoked = parse_revocations(raw)
        if revoked is None:
            self._warn(f"{self._path} does not hold a revocation list")
            return
        self._warned = False
        now = time.time()
        self._state = _FileState(
            signature=signature,
            revoked={sid: until for sid, until in revoked.items() if until > now},
        )

    async def async_is_revoked(self, hass: HomeAssistant, sid: int) -> bool:
        """Whether ``sid`` is revoked now, re-reading the file if it is due.

        Callers arriving while a re-read is in flight wait for it under the
        lock: answering them from the cache it is replacing would let a revoked
        agent through by sending its requests in parallel.
        """
        async with self._refresh_lock:
            if time.monotonic() - self._checked_at >= _RECHECK_SECONDS:
                await hass.async_add_executor_job(self.refresh)
                self._checked_at = time.monotonic()
        return self.is_revoked(sid)

    def is_revoked(self, sid: int) -> bool:
        """Whether ``sid`` is listed with an ``until`` still in the future."""
        until = self._state.revoked.get(sid)
        if until is None:
            return False
        if until <= time.time():
            del self._state.revoked[sid]
            return False
        return True

    def _warn(self, reason: str) -> None:
        # Once per failure, not on every request carrying a sid.
        if not self._warned:
            self._warned = True
            _LOGGER.warning("Revoked MCP sessions: %s; keeping the last list read", reason)


def ensure_ack(directory: Path) -> str:
    """Declare to the OS that this integration reads the list.

    Blocking — use an executor. Returns "" on success, or why it failed.
    """
    desired = json.dumps({"channel": REVOCATION_CHANNEL_VERSION}, sort_keys=True).encode() + b"\n"
    path = directory / ACK_FILE_NAME
    try:
        if path.read_bytes() == desired:
            return ""
    except OSError:
        pass  # Missing or unreadable — write it out below.

    tmp = directory / f".{ACK_FILE_NAME}.{uuid4().hex}.tmp"
    try:
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as fh:
            fh.write(desired)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except OSError as err:
        with suppress(OSError):
            tmp.unlink()
        return f"{path} could not be written ({err})"
    return ""
