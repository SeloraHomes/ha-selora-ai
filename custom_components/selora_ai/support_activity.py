"""What an installer changed during a Connect support session, for Connect.

The integration never talks to Connect: each event is a file in an outbox
(``<config>/selora_ai/support_activity/``) that Selora OS reads from the VM and
forwards with the installation's token. See ``docs/dev/support-activity.md``.

One file per event, written atomically and never changed after. Names sort by
time, so the host keeps a cursor (the last name it sent) and never writes into
the VM; Connect drops repeats by ``event_id``. The outbox prunes itself by age
and count, since a hub without Selora OS has nothing reading it.
"""

from __future__ import annotations

from contextlib import suppress
from datetime import UTC, datetime, timedelta
import json
import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final
import uuid

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

OUTBOX_DIR: Final = ("selora_ai", "support_activity")
SCHEMA_VERSION: Final = 1
_KEEP: Final = timedelta(days=30)
_MAX_FILES: Final = 2000
_SUFFIX: Final = ".json"


def outbox_path(hass: HomeAssistant) -> Path:
    return Path(hass.config.path(*OUTBOX_DIR))


async def async_record(
    hass: HomeAssistant, session_id: int, event_type: str, data: dict[str, Any]
) -> None:
    """Queue one event of support session ``session_id`` for Connect.

    Never raises: losing the report must not fail the change it reports. A
    failure is logged, and the WARNING log line of the change itself stays.
    """
    now = datetime.now(UTC)
    event_id = uuid.uuid4().hex
    event = {
        "schema_version": SCHEMA_VERSION,
        "event_id": event_id,
        "session_id": session_id,
        "occurred_at": now.isoformat(),
        "type": event_type,
        **data,
    }
    name = f"{now.strftime('%Y%m%dT%H%M%S%fZ')}-{event_id}{_SUFFIX}"
    try:
        await hass.async_add_executor_job(_write, outbox_path(hass), name, event, now)
    except OSError as exc:
        _LOGGER.warning(
            "Selora AI: could not queue support-session event %s for Connect: %s",
            event_type,
            exc,
        )


def _write(outbox: Path, name: str, event: dict[str, Any], now: datetime) -> None:
    outbox.mkdir(parents=True, exist_ok=True)
    tmp = outbox / f".{name}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(json.dumps(event, separators=(",", ":")).encode("utf-8"))
        fh.flush()
        os.fsync(fh.fileno())
    # The rename is the commit point: the host only reads `*.json`.
    os.replace(tmp, outbox / name)
    _prune(outbox, now)


def _prune(outbox: Path, now: datetime) -> None:
    """Drop events older than ``_KEEP``, then the oldest past ``_MAX_FILES``."""
    cutoff = (now - _KEEP).strftime("%Y%m%dT%H%M%S%fZ")
    names = sorted(p.name for p in outbox.glob(f"*{_SUFFIX}"))
    stale = [n for n in names if n < cutoff]
    kept = len(names) - len(stale)
    stale += [n for n in names if n >= cutoff][: max(0, kept - _MAX_FILES)]
    for stale_name in stale:
        with suppress(FileNotFoundError):
            (outbox / stale_name).unlink()
