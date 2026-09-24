"""The Alexa voice credential, handed over as a file instead of in the entry.

Selora OS provisions voice and has to get the credential to us. It used to do
that by writing our config entry, which means editing
``.storage/core.config_entries`` directly — safe only with Home Assistant
stopped. So the reward for linking the skill was the owner's home going down
for minutes, unannounced, with voice unable to answer until it came back.

The OS now also writes the credential to a file in the configuration
directory, and this module reads it. Applying it here costs nothing: the
validator is rebuilt in place, no entry is written and nothing restarts.

The file is a TRANSPORT, not a staging area for the entry. Copying what it
carries into the config entry is the obvious implementation and it puts every
hub into a restart loop: once the OS sees our ack it stops putting the Alexa
keys in its *desired* entry data, and its reconciler compares desired against
the live entry key by key — ``selora_alexa_*`` included. Keys we add that
desired does not have read as drift, and drift is repaired by stopping Core.
The fix, rebuilt out of its own fix.

The channel
-----------
``alexa_credential.json`` is written by the OS, ``alexa_applied.json`` by us.
The ack is what lets the OS stop writing the entry: hub and integration update
on separate schedules, so a host that stopped writing the keys to a hub whose
integration cannot read the file would have removed that home's voice
assistant. Until the ack appears the OS keeps doing exactly what it does
today, which is why this needs no coordinated release.

``channel`` declares the capability and is a SEPARATE claim from having
applied anything — a rotating credential is momentarily un-applied, and a host
that read the two as one question would put the keys back into the entry and
take the restart on every rotation, which is the case this exists for. So the
ack is written on every setup, credential or no credential, and
``applied_digest`` is added once one has actually been applied.

The digest is over the file's RAW BYTES as read. The OS digests the bytes it
wrote, which end in a newline; hashing a re-serialised copy of the parsed JSON
gives a different value and the hub reports voice as ``delivering`` forever.

Nothing here logs at all. Both entry points hand their failure back as a
string for the caller to report, which is what lets a complaint be written on
the transition rather than on every one of the poll's reads — and it keeps the
one module that holds the credential's bytes away from the logger entirely.
"""

from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
from typing import TYPE_CHECKING, Literal
from uuid import uuid4

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

# The channel version we implement. The OS gates on this exact value and
# treats one it does not know as no ack at all, so bumping it is a promise
# that the payload shape changed.
ALEXA_CHANNEL_VERSION = 1

# Beside `.storage`, in the configuration directory, because that is where the
# credential already lives — anything able to read this file could already
# read it out of the config entry. The share folder would take a key confined
# to this directory and publish it to every add-on that maps share.
CREDENTIAL_DIR_NAME = ".selora"
CREDENTIAL_FILE_NAME = "alexa_credential.json"
ACK_FILE_NAME = "alexa_applied.json"

# How often the file is re-read. The credential arrives minutes after the
# owner links the skill, so reading once at setup would make them restart to
# pick it up — the problem restated. Well inside the OS's own config sync, and
# a couple of hundred bytes either way.
CREDENTIAL_POLL_SECONDS = 30

# Every member the validator is built from, all of them required. Unlike the
# config entry there is no history here to be tolerant of: the OS emits the
# five together or emits nothing, so a member missing from a file that exists
# means the bytes are not whole.
_REQUIRED_FIELDS = ("jwt_key", "audience", "scope", "issuer", "installation_id")


@dataclass(frozen=True, slots=True)
class FileCredential:
    """A credential read whole from the file, with the digest to ack it by."""

    credentials: dict[str, str]
    digest: str


@dataclass(frozen=True, slots=True)
class CredentialRead:
    """What one read of the credential file found.

    ``absent`` and ``unreadable`` are deliberately different answers. An
    absent file is a WITHDRAWAL — the OS removes it when voice is taken away —
    while an unreadable one says nothing about whether voice is still
    provisioned, so it must leave a working validator standing rather than
    revoke it. The OS renames the file into place, so a torn read should not
    happen; a corrupted or half-restored file is not a reason to take the
    home's voice assistant off it.
    """

    state: Literal["present", "absent", "unreadable"]
    credential: FileCredential | None = None
    # Why it was unreadable, in words that never quote the file. The caller
    # owns the logging because it is the only side that can see a transition:
    # the poll re-reads every 30 seconds, so a line written here would repeat
    # for as long as the file stays broken.
    reason: str = ""


def credential_dir(hass: HomeAssistant) -> Path:
    """The directory the OS delivers into."""
    return Path(hass.config.path(CREDENTIAL_DIR_NAME))


def read_credential(directory: Path) -> CredentialRead:
    """Read and validate the delivered credential. Blocking — use an executor."""
    path = directory / CREDENTIAL_FILE_NAME
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return CredentialRead("absent")
    except OSError as err:
        # Unreadable is not absent: a directory we cannot enter — the mode or
        # ownership having drifted — must not read as a withdrawal. `err` is an
        # errno and a path, never content.
        return CredentialRead("unreadable", reason=f"{path} could not be read ({err})")

    parsed = _parse(raw)
    if parsed is None:
        # No detail derived from what was in the file: an error quoting the
        # offending bytes is the natural way to write this one, and those bytes
        # are the credential.
        return CredentialRead("unreadable", reason=f"{path} does not hold a whole credential")

    return CredentialRead(
        "present",
        FileCredential(credentials=parsed, digest=hashlib.sha256(raw).hexdigest()),
    )


def _parse(raw: bytes) -> dict[str, str] | None:
    """The five delivered fields, in the shape the validator is built from.

    ``None`` for anything that is not a whole credential. No error text
    derived from the content is returned or logged — a parse error that quoted
    the offending bytes is one refactor away from printing the key.
    """
    try:
        payload = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(payload, dict):
        return None

    values: dict[str, str] = {}
    for field in _REQUIRED_FIELDS:
        value = payload.get(field)
        # `installation_id` is the ONLY member that arrives as a JSON number —
        # Connect marshals an int64. Accepting a number anywhere else turns
        # `"jwt_key": 123` into the string "123", which is a credential shaped
        # well enough to replace the last good one in the cache and then raise
        # out of `decode_jwt_key` on every poll. bool is an int in Python and
        # is not an installation id.
        if field == "installation_id":
            if isinstance(value, bool) or not isinstance(value, (str, int)):
                return None
        elif not isinstance(value, str):
            return None
        text = str(value).strip()
        if not text:
            return None
        values[field] = text

    return {
        "key": values["jwt_key"],
        "installation_id": values["installation_id"],
        "issuer": values["issuer"],
        "audience": values["audience"],
        "scope": values["scope"],
    }


def ensure_ack(directory: Path, applied_digest: str | None) -> str:
    """Make the ack on disk say what we can do and what we have applied.

    Blocking — use an executor. Returns "" on success, or why it failed.
    Compared against the bytes already there and rewritten only on a
    difference, because this runs on every poll and the OS reads the file on
    every pass of its own.
    """
    desired = json.dumps(_ack_payload(applied_digest), sort_keys=True).encode() + b"\n"
    path = directory / ACK_FILE_NAME
    try:
        if path.read_bytes() == desired:
            return ""
    except OSError:
        pass  # Missing, unreadable or a partial write — write it out below.

    try:
        # 0700 because the credential sits in here; created by the OS in the
        # ordinary case, by us when we are declaring the capability before one
        # has ever been delivered.
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        # A name of its own per write. Two syncs can be in flight at once — a
        # second entry setting up while the poll runs — and on one shared temp
        # name the later `O_TRUNC` empties the file the earlier one is about to
        # rename into place, publishing a truncated ack. Selora OS reads a
        # garbled ack as no ack at all, which puts the credential back in the
        # config entry and costs the restart. Both renames are atomic, so the
        # loser is simply overwritten by the next poll.
        _atomic_bytes(directory / f".{ACK_FILE_NAME}.{uuid4().hex}.tmp", path, desired)
    except OSError as err:
        return f"{path} could not be written ({err})"
    return ""


def _ack_payload(applied_digest: str | None) -> dict[str, object]:
    payload: dict[str, object] = {"channel": ALEXA_CHANNEL_VERSION}
    if applied_digest:
        payload["applied_digest"] = applied_digest
    return payload


def _atomic_bytes(tmp: Path, dest: Path, data: bytes) -> None:
    """Write ``data`` to ``tmp`` then rename onto ``dest``.

    The OS reads this file while we write it, so the rename is what keeps it
    reading a whole one. 0600 explicitly rather than by umask, and carried
    onto ``dest`` by the rename.
    """
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as fh:
            os.fchmod(fh.fileno(), 0o600)  # explicit: covers a pre-existing tmp
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
    except OSError:
        # A half-written temp file left behind would be picked up and
        # overwritten by the next attempt, but it is ours to clean up.
        with suppress(OSError):
            tmp.unlink()
        raise
    os.replace(tmp, dest)


__all__ = [
    "ACK_FILE_NAME",
    "ALEXA_CHANNEL_VERSION",
    "CREDENTIAL_DIR_NAME",
    "CREDENTIAL_FILE_NAME",
    "CREDENTIAL_POLL_SECONDS",
    "CredentialRead",
    "FileCredential",
    "credential_dir",
    "ensure_ack",
    "read_credential",
]
