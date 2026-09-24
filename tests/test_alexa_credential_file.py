"""The voice credential arrives in a file, and applying it restarts nothing.

Selora OS used to hand the credential over by writing our config entry, which
means editing `.storage/core.config_entries` directly — safe only with Home
Assistant stopped. So linking the skill took the owner's home down for minutes,
unannounced, with voice unable to answer until it came back.

The OS now writes the credential to `<config>/.selora/alexa_credential.json`
and reads back an ack naming the channel we speak. Until it sees that ack it
keeps putting the keys in the entry exactly as before, which is what lets hub
and integration update on their own schedules — so the entry fallback below is
not legacy tolerance, it is what every hub in the field is still doing.

The one thing this must not do is write what it reads back into the config
entry. The OS reconciler compares its desired entry data against the live one
key by key, `selora_alexa_*` included, and once the ack is there its desired
data carries none — so a key we add reads as drift, and drift is repaired by
stopping Core. That is the restart this whole change removes, rebuilt out of
its own fix.
"""

from __future__ import annotations

import asyncio
import base64
from datetime import timedelta
import hashlib
import hmac
import json
from pathlib import Path
import shutil
from typing import Any
from unittest.mock import patch

from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.selora_ai import (
    _alexa_credentials,
    _async_poll_alexa_credential,
    _async_sync_alexa_runtime,
    async_setup_entry,
)
from custom_components.selora_ai.alexa_credential_file import (
    ACK_FILE_NAME,
    CREDENTIAL_FILE_NAME,
    CREDENTIAL_POLL_SECONDS,
)
from custom_components.selora_ai.const import (
    CONF_ENTRY_TYPE,
    CONF_SELORA_ALEXA_AUDIENCE,
    CONF_SELORA_ALEXA_ISSUER,
    CONF_SELORA_ALEXA_JWT_KEY,
    CONF_SELORA_ALEXA_SCOPE,
    CONF_SELORA_INSTALLATION_ID,
    DOMAIN,
    ENTRY_TYPE_LLM,
)

INSTALLATION_ID = 77
# Never a real Pangolin subdomain, here or anywhere else in the repo.
ALEXA_ISSUER = "https://alexa-xxx-xxx.example.test"
FILE_KEY = hmac.new(b"file-epoch", b"alexa-auth:77", "sha256").digest()
FILE_KEY_B64 = base64.b64encode(FILE_KEY).decode()
ENTRY_KEY = hmac.new(b"entry-epoch", b"alexa-auth:77", "sha256").digest()
ENTRY_KEY_B64 = base64.b64encode(ENTRY_KEY).decode()


def _payload_bytes(*, key: str = FILE_KEY_B64, scope: str = "alexa:directive") -> bytes:
    """The credential exactly as the OS writes it.

    Byte-identical on purpose: `jq -cS` renders it compact and key-sorted, and
    the shell writes it with `printf '%s\\n'`, so the file ends in a newline.
    The digest both sides compare is over these bytes — hashing a re-serialised
    copy of the parsed JSON gives a different value, and the hub then reports
    voice as `delivering` for ever.
    """
    return (
        f'{{"audience":"selora-alexa","installation_id":{INSTALLATION_ID},'
        f'"issuer":"{ALEXA_ISSUER}","jwt_key":"{key}","scope":"{scope}"}}\n'
    ).encode()


def _selora_dir(hass: Any) -> Path:
    return Path(hass.config.path(".selora"))


def _deliver(hass: Any, raw: bytes = None) -> bytes:
    """Put a credential where the OS puts one, as the OS puts it."""
    raw = _payload_bytes() if raw is None else raw
    directory = _selora_dir(hass)
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    (directory / CREDENTIAL_FILE_NAME).write_bytes(raw)
    return raw


def _withdraw(hass: Any) -> None:
    (_selora_dir(hass) / CREDENTIAL_FILE_NAME).unlink()


def _ack(hass: Any) -> dict[str, Any]:
    return json.loads((_selora_dir(hass) / ACK_FILE_NAME).read_bytes())


async def _tick(hass: Any) -> None:
    """Let the credential poll run once.

    ``wait_background_tasks`` because the interval job IS a background task —
    a poll that every ``async_block_till_done()`` waited on would put its
    interval into bootstrap, every reload, and every test in the suite.
    """
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=CREDENTIAL_POLL_SECONDS + 1))
    await hass.async_block_till_done(wait_background_tasks=True)


def _entry_data(**overrides: Any) -> dict[str, Any]:
    """An entry carrying no Alexa keys — what the OS writes once it is acked.

    No LLM provider either: voice is provisioned independently of Selora AI, so
    a customer can pay for one and not the other, and this is the entry that
    customer's hub has.
    """
    data: dict[str, Any] = {
        CONF_ENTRY_TYPE: ENTRY_TYPE_LLM,
        CONF_SELORA_INSTALLATION_ID: str(INSTALLATION_ID),
    }
    data.update(overrides)
    return data


def _entry_alexa_data() -> dict[str, Any]:
    """A pre-channel entry: the credential still travels inside it."""
    return _entry_data(
        **{
            CONF_SELORA_ALEXA_JWT_KEY: ENTRY_KEY_B64,
            CONF_SELORA_ALEXA_AUDIENCE: "selora-alexa",
            CONF_SELORA_ALEXA_SCOPE: "alexa:directive",
            CONF_SELORA_ALEXA_ISSUER: ALEXA_ISSUER,
        }
    )


@pytest.fixture(autouse=True)
def _clean_selora_dir(hass: Any) -> Any:
    """The test harness shares one config directory across this module.

    Home Assistant's own `.selora` is per-install and persists on purpose, so
    without this a credential delivered by one test is still there for the
    next and the ack it left behind answers a question that test never asked.
    """
    shutil.rmtree(_selora_dir(hass), ignore_errors=True)
    yield
    directory = _selora_dir(hass)
    if directory.exists():
        directory.chmod(0o700)
    shutil.rmtree(directory, ignore_errors=True)


def _add(hass: Any, data: dict[str, Any]) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, data=data)
    entry.add_to_hass(hass)
    return entry


# ── The file is the source ────────────────────────────────────────────────────


async def test_a_delivered_file_builds_the_validator(hass: Any) -> None:
    """The point of the change: the entry carries no Alexa keys at all, and
    voice still comes up."""
    _add(hass, _entry_data())
    _deliver(hass)

    await _async_sync_alexa_runtime(hass)

    validator = hass.data[DOMAIN]["selora_alexa_jwt_validator"]
    assert validator is not None
    assert _alexa_credentials(hass) == {
        "key": FILE_KEY_B64,
        "installation_id": str(INSTALLATION_ID),
        "issuer": ALEXA_ISSUER,
        "audience": "selora-alexa",
        "scope": "alexa:directive",
    }


async def test_the_entry_still_works_when_no_file_exists(hass: Any) -> None:
    """A hub whose OS predates the channel gets its credential in the entry and
    has to keep working — and so does every hub that has not yet been acked."""
    _add(hass, _entry_alexa_data())

    await _async_sync_alexa_runtime(hass)

    assert hass.data[DOMAIN]["selora_alexa_jwt_validator"] is not None
    assert _alexa_credentials(hass)["key"] == ENTRY_KEY_B64


async def test_the_file_wins_when_both_exist(hass: Any) -> None:
    """The overlap is the whole migration window: the OS delivers the file on
    the pass before it drops the keys from the entry, and the two are the same
    credential at different ages. The file is the fresher road."""
    _add(hass, _entry_alexa_data())
    _deliver(hass)

    await _async_sync_alexa_runtime(hass)

    assert _alexa_credentials(hass)["key"] == FILE_KEY_B64


async def test_the_file_is_never_written_into_the_config_entry(hass: Any) -> None:
    """The failure this change must not rebuild. Selora OS compares its desired
    entry data against the live entry key by key — `selora_alexa_*` included —
    and once the ack is there its desired data carries none of them. A key we
    add reads as drift, and drift is repaired by stopping Core: a restart on
    every reconcile, which is the exact thing being removed here."""
    entry = _add(hass, _entry_data())
    _deliver(hass)

    await _async_sync_alexa_runtime(hass)

    assert hass.data[DOMAIN]["selora_alexa_jwt_validator"] is not None
    assert not [key for key in entry.data if key.startswith("selora_alexa")]


async def test_a_withdrawn_file_clears_the_validator(hass: Any) -> None:
    """The OS removes the file when voice is withdrawn, and that is the whole
    revoke path — there is no second one to keep in step."""
    _add(hass, _entry_data())
    _deliver(hass)
    await _async_sync_alexa_runtime(hass)
    assert hass.data[DOMAIN]["selora_alexa_jwt_validator"] is not None

    _withdraw(hass)
    await _async_sync_alexa_runtime(hass)

    assert hass.data[DOMAIN]["selora_alexa_jwt_validator"] is None


async def test_a_withdrawn_file_falls_back_to_the_entry(hass: Any) -> None:
    """Withdrawal clears the FILE, not voice. A hub the OS has not yet acked
    still has its credential in the entry, and a file that never arrives — or
    goes away again — must leave that one answering."""
    _add(hass, _entry_alexa_data())
    _deliver(hass)
    await _async_sync_alexa_runtime(hass)
    assert _alexa_credentials(hass)["key"] == FILE_KEY_B64

    _withdraw(hass)
    await _async_sync_alexa_runtime(hass)

    assert _alexa_credentials(hass)["key"] == ENTRY_KEY_B64


@pytest.mark.parametrize(
    "raw",
    [
        pytest.param(b'{"audience":"selora-alexa","installation', id="truncated"),
        pytest.param(b"", id="empty"),
        pytest.param(b'{"audience":"selora-alexa"}\n', id="missing-members"),
        pytest.param(b'["not","an","object"]\n', id="wrong-shape"),
        pytest.param(
            b'{"audience":"selora-alexa","installation_id":77,"issuer":"",'
            b'"jwt_key":"k","scope":"alexa:directive"}\n',
            id="blank-member",
        ),
    ],
)
async def test_a_malformed_file_leaves_a_working_validator_standing(hass: Any, raw: bytes) -> None:
    """Absent and unreadable are different answers. An absent file is the OS
    saying voice is withdrawn; an unreadable one says nothing about whether
    voice is provisioned, so it must not take the home's voice assistant off it
    — a half-restored or corrupted file is not a revocation."""
    _add(hass, _entry_data())
    _deliver(hass)
    await _async_sync_alexa_runtime(hass)
    working = hass.data[DOMAIN]["selora_alexa_jwt_validator"]
    assert working is not None

    _deliver(hass, raw)
    await _async_sync_alexa_runtime(hass)

    assert hass.data[DOMAIN]["selora_alexa_jwt_validator"] is not None
    assert _alexa_credentials(hass)["key"] == FILE_KEY_B64


# ── The ack ───────────────────────────────────────────────────────────────────


async def test_the_ack_digest_is_over_the_raw_bytes(hass: Any) -> None:
    """The OS digests the bytes it wrote, trailing newline and all. Hashing a
    re-serialised copy of the parsed JSON produces a different value and the
    hub reports voice as `delivering` for ever."""
    _add(hass, _entry_data())
    raw = _deliver(hass)

    await _async_sync_alexa_runtime(hass)

    assert _ack(hass) == {
        "channel": 1,
        "applied_digest": hashlib.sha256(raw).hexdigest(),
    }
    # And it is genuinely the bytes, not the parsed object re-rendered.
    reserialised = json.dumps(json.loads(raw), sort_keys=True, separators=(",", ":")).encode()
    assert hashlib.sha256(reserialised).hexdigest() != _ack(hass)["applied_digest"]


async def test_the_channel_is_declared_with_no_credential_at_all(hass: Any) -> None:
    """The channel declares the CAPABILITY, which is what lets the OS stop
    writing the entry — a separate claim from having applied anything. Gated on
    the two together, a rotating credential would be momentarily un-acked and
    the OS would put the keys back and take the restart on every rotation."""
    _add(hass, _entry_data())

    await _async_sync_alexa_runtime(hass)

    assert _ack(hass) == {"channel": 1}


async def test_the_entry_path_declares_the_channel_but_applies_nothing(hass: Any) -> None:
    """A hub still served by its config entry can read the file and has not
    applied anything through it. Reporting a digest there would have the OS
    call voice ready on a credential it never delivered."""
    _add(hass, _entry_alexa_data())

    await _async_sync_alexa_runtime(hass)

    assert hass.data[DOMAIN]["selora_alexa_jwt_validator"] is not None
    assert _ack(hass) == {"channel": 1}


async def test_a_refused_credential_is_not_reported_as_applied(hass: Any) -> None:
    """`applied_digest` is the only guest-observed answer to "is voice actually
    ready", so it follows the validator rather than the delivery. A credential
    that cannot be separated from the MCP one builds nothing."""
    _add(hass, _entry_data())
    _deliver(hass, _payload_bytes(scope="mcp:write"))

    await _async_sync_alexa_runtime(hass)

    assert hass.data[DOMAIN]["selora_alexa_jwt_validator"] is None
    assert _ack(hass) == {"channel": 1}


async def test_the_ack_is_written_on_setup(hass: Any) -> None:
    """On every setup, so a hub that has never been delivered to still declares
    the capability — that declaration is what stops the OS writing the entry."""
    from homeassistant.setup import async_setup_component

    assert await async_setup_component(hass, "http", {})
    entry = _add(hass, _entry_data())

    assert await async_setup_entry(hass, entry) is True

    assert _ack(hass) == {"channel": 1}


async def test_the_ack_directory_is_private(hass: Any) -> None:
    """The credential sits in here. Created 0700 with the ack 0600, matching
    what the OS creates when it delivers first."""
    _add(hass, _entry_data())

    await _async_sync_alexa_runtime(hass)

    directory = _selora_dir(hass)
    assert directory.stat().st_mode & 0o777 == 0o700
    assert (directory / ACK_FILE_NAME).stat().st_mode & 0o777 == 0o600


async def test_an_unwritable_directory_says_so_loudly(hass: Any, caplog: Any) -> None:
    """Without the ack the OS keeps delivering in the config entry: voice still
    works and the restart quietly stays. That is exactly the failure that hides
    itself, so it is reported rather than swallowed.

    The write failure is injected rather than produced with `chmod`. Core runs
    as root, and so does CI, and root writes through a 0500 directory — so the
    realistic failure is a read-only or full filesystem, and a permission-based
    test passes for an unprivileged developer while proving nothing where it
    matters.
    """
    from custom_components.selora_ai import alexa_credential_file

    _add(hass, _entry_data())

    def _read_only(*_args: Any) -> None:
        raise OSError(30, "Read-only file system")

    with (
        patch.object(alexa_credential_file, "_atomic_bytes", _read_only),
        caplog.at_level("ERROR"),
    ):
        await _async_sync_alexa_runtime(hass)

    assert any("channel ack" in record.getMessage() for record in caplog.records)
    assert not (_selora_dir(hass) / ACK_FILE_NAME).exists()


async def test_an_ack_failure_is_reported_once_not_every_poll(hass: Any, caplog: Any) -> None:
    """The poll asks again every 30 seconds, and a read-only filesystem stays
    read-only until somebody fixes it."""
    from custom_components.selora_ai import alexa_credential_file

    _add(hass, _entry_data())

    def _read_only(*_args: Any) -> None:
        raise OSError(30, "Read-only file system")

    with (
        patch.object(alexa_credential_file, "_atomic_bytes", _read_only),
        caplog.at_level("ERROR"),
    ):
        await _async_sync_alexa_runtime(hass)
        await _async_sync_alexa_runtime(hass)
        await _async_sync_alexa_runtime(hass)

    assert sum("channel ack" in r.getMessage() for r in caplog.records) == 1


# ── Noticing a change without a restart ───────────────────────────────────────


async def test_a_credential_delivered_after_setup_is_picked_up(hass: Any) -> None:
    """The regression that matters to the owner. The credential lands minutes
    after they link the skill, and making them restart to pick it up is the
    problem this channel removes, restated."""
    from homeassistant.setup import async_setup_component

    assert await async_setup_component(hass, "http", {})
    entry = _add(hass, _entry_data())
    await async_setup_entry(hass, entry)
    assert hass.data[DOMAIN]["selora_alexa_jwt_validator"] is None

    _deliver(hass)
    await _tick(hass)

    assert hass.data[DOMAIN]["selora_alexa_jwt_validator"] is not None
    assert _ack(hass)["applied_digest"] == hashlib.sha256(_payload_bytes()).hexdigest()


async def test_a_withdrawal_after_setup_is_picked_up(hass: Any) -> None:
    """The other direction, on the same timer: the OS removes the file and
    voice stops answering without anything being reloaded."""
    from homeassistant.setup import async_setup_component

    assert await async_setup_component(hass, "http", {})
    entry = _add(hass, _entry_data())
    _deliver(hass)
    await async_setup_entry(hass, entry)
    assert hass.data[DOMAIN]["selora_alexa_jwt_validator"] is not None

    _withdraw(hass)
    await _tick(hass)

    assert hass.data[DOMAIN]["selora_alexa_jwt_validator"] is None


async def test_a_rotated_credential_is_picked_up_without_a_restart(hass: Any) -> None:
    """Rotation is the case the whole channel exists for: unlink and relink in
    Connect used to cost the owner a Core restart."""
    from homeassistant.setup import async_setup_component

    assert await async_setup_component(hass, "http", {})
    entry = _add(hass, _entry_data())
    _deliver(hass)
    await async_setup_entry(hass, entry)

    rotated_key = base64.b64encode(
        hmac.new(b"next-epoch", b"alexa-auth:77", "sha256").digest()
    ).decode()
    rotated = _deliver(hass, _payload_bytes(key=rotated_key))
    await _tick(hass)

    assert _alexa_credentials(hass)["key"] == rotated_key
    assert _ack(hass)["applied_digest"] == hashlib.sha256(rotated).hexdigest()


async def test_unloading_the_last_entry_stops_the_poll(hass: Any) -> None:
    """The timer is fleet-level, like the validator it feeds. Left armed it
    would keep rebuilding a validator off the file for an integration that is
    no longer loaded — voice accepting directives with nothing behind it."""
    from homeassistant.setup import async_setup_component

    from custom_components.selora_ai import async_unload_entry

    assert await async_setup_component(hass, "http", {})
    entry = _add(hass, _entry_data())
    _deliver(hass)
    await async_setup_entry(hass, entry)
    assert hass.data[DOMAIN]["selora_alexa_jwt_validator"] is not None

    await async_unload_entry(hass, entry)
    assert hass.data[DOMAIN]["selora_alexa_jwt_validator"] is None

    await _tick(hass)

    assert hass.data[DOMAIN]["selora_alexa_jwt_validator"] is None


async def test_a_disabled_entry_does_not_re_enable_voice_from_the_file(hass: Any) -> None:
    """The file outlives any one entry, so on its own it would answer for a hub
    whose integration the user has switched off."""
    from homeassistant.config_entries import ConfigEntryDisabler

    disabled = MockConfigEntry(
        domain=DOMAIN, data=_entry_data(), disabled_by=ConfigEntryDisabler.USER
    )
    disabled.add_to_hass(hass)
    _deliver(hass)

    await _async_sync_alexa_runtime(hass)

    assert hass.data[DOMAIN]["selora_alexa_jwt_validator"] is None
    assert _alexa_credentials(hass) is None


# ── The key never reaches the log ─────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw",
    [
        pytest.param(None, id="whole"),
        pytest.param(
            b'{"audience":"selora-alexa","jwt_key":"' + FILE_KEY_B64.encode() + b'"', id="truncated"
        ),
    ],
)
async def test_nothing_logs_the_key_or_the_file(hass: Any, caplog: Any, raw: bytes) -> None:
    """Asserted against real output rather than trusted. The truncated case is
    the one that invites it: an error quoting the offending bytes is the
    natural way to write it, and those bytes are the credential."""
    _add(hass, _entry_data())
    delivered = _deliver(hass, raw) if raw is not None else _deliver(hass)

    with caplog.at_level("DEBUG"):
        await _async_sync_alexa_runtime(hass)

    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert FILE_KEY_B64 not in logged
    assert delivered.decode(errors="replace") not in logged


# ── The outbound half reads the same file ─────────────────────────────────────


async def test_the_connect_client_is_built_from_the_delivered_file(hass: Any) -> None:
    """Proactive reporting signs with the same credential the validator checks,
    and once the OS is acked the entry carries none. An entry-only walk would
    find nothing and take reporting down while directives kept being answered —
    the quietest way for half of voice to stop working."""
    from custom_components.selora_ai.alexa_connect import build_client

    _add(hass, _entry_data())
    _deliver(hass)
    await _async_sync_alexa_runtime(hass)

    client = build_client(hass, hass.data[DOMAIN])

    assert client is not None
    assert client._installation_id == str(INSTALLATION_ID)
    assert client._derived_key == base64.b64decode(FILE_KEY_B64)


async def test_the_connect_client_still_falls_back_to_the_entry(hass: Any) -> None:
    _add(hass, _entry_alexa_data())

    from custom_components.selora_ai.alexa_connect import build_client

    await _async_sync_alexa_runtime(hass)
    client = build_client(hass, hass.data[DOMAIN])

    assert client is not None
    assert client._derived_key == base64.b64decode(ENTRY_KEY_B64)


async def test_the_installation_id_comes_off_the_file_when_the_entry_has_none(
    hass: Any,
) -> None:
    """A voice-only hub can lose the id from its entry altogether once the OS
    stops writing the Alexa block, and the delivered file carries its own."""
    from custom_components.selora_ai.alexa_view import _installation_id

    _add(hass, {CONF_ENTRY_TYPE: ENTRY_TYPE_LLM})
    _deliver(hass)
    await _async_sync_alexa_runtime(hass)

    assert _installation_id(hass) == str(INSTALLATION_ID)


async def test_a_broken_file_is_reported_once_not_every_poll(hass: Any, caplog: Any) -> None:
    """The poll re-reads every 30 seconds and a broken file stays broken until
    somebody fixes it, so a line written per read is thousands a day. Logged on
    the transition instead — and logged again once it changes to a different
    complaint."""
    from homeassistant.setup import async_setup_component

    assert await async_setup_component(hass, "http", {})
    entry = _add(hass, _entry_data())
    _deliver(hass, b'{"audience":"selora-alexa"}\n')
    await async_setup_entry(hass, entry)

    with caplog.at_level("WARNING"):
        await _tick(hass)
        await _tick(hass)

    ignored = [r for r in caplog.records if "Ignoring the delivered" in r.getMessage()]
    assert len(ignored) == 1


async def test_a_repaired_file_can_be_reported_broken_again(hass: Any, caplog: Any) -> None:
    """The transition is remembered, not the fact that we once complained — a
    file that breaks, is fixed, and breaks again has to say so each time."""
    _add(hass, _entry_data())
    _deliver(hass, b"{")
    await _async_sync_alexa_runtime(hass)
    _deliver(hass)
    await _async_sync_alexa_runtime(hass)

    with caplog.at_level("WARNING"):
        _deliver(hass, b"{")
        await _async_sync_alexa_runtime(hass)

    assert any("Ignoring the delivered" in r.getMessage() for r in caplog.records)


# ── The relink is the other door into the entry ───────────────────────────────


async def test_a_connect_relink_does_not_put_the_credential_back(hass: Any) -> None:
    """The restart loop, reached through the panel rather than the sync.
    `exchange_connect_code` writes the Alexa block into the entry on every
    relink, and once the OS is acked its desired data carries none — so those
    four keys read as drift and are repaired by stopping Core, on every
    reconcile. Guarding only the credential sync leaves this door open."""
    from custom_components.selora_ai.oauth_link import _apply_alexa_block

    entry_data: dict[str, Any] = {"other": "kept"}
    _apply_alexa_block(
        entry_data,
        {
            CONF_SELORA_ALEXA_JWT_KEY: FILE_KEY_B64,
            CONF_SELORA_ALEXA_AUDIENCE: "selora-alexa",
            CONF_SELORA_ALEXA_SCOPE: "alexa:directive",
            CONF_SELORA_ALEXA_ISSUER: ALEXA_ISSUER,
        },
        answered=True,
        delivered_by_file=True,
    )

    assert entry_data == {"other": "kept"}


async def test_a_relink_clears_keys_the_file_has_taken_over(hass: Any) -> None:
    """Not merely "does not write": a stale set left behind is the same drift,
    since the comparison is against the live entry rather than against what
    this relink did."""
    from custom_components.selora_ai.oauth_link import _apply_alexa_block

    entry_data: dict[str, Any] = {
        CONF_SELORA_ALEXA_JWT_KEY: ENTRY_KEY_B64,
        CONF_SELORA_ALEXA_AUDIENCE: "selora-alexa",
        CONF_SELORA_ALEXA_SCOPE: "alexa:directive",
        CONF_SELORA_ALEXA_ISSUER: ALEXA_ISSUER,
        CONF_SELORA_INSTALLATION_ID: str(INSTALLATION_ID),
    }
    _apply_alexa_block(entry_data, None, answered=False, delivered_by_file=True)

    assert entry_data == {CONF_SELORA_INSTALLATION_ID: str(INSTALLATION_ID)}


async def test_a_relink_on_a_hub_with_no_file_still_writes_the_entry(hass: Any) -> None:
    """The pre-!111 hub. Its OS delivers in the entry and reads no ack, so the
    relink has to keep filling it in — the default is today's behaviour."""
    from custom_components.selora_ai.oauth_link import _apply_alexa_block

    block = {
        CONF_SELORA_ALEXA_JWT_KEY: ENTRY_KEY_B64,
        CONF_SELORA_ALEXA_AUDIENCE: "selora-alexa",
        CONF_SELORA_ALEXA_SCOPE: "alexa:directive",
        CONF_SELORA_ALEXA_ISSUER: ALEXA_ISSUER,
    }
    entry_data: dict[str, Any] = {}
    _apply_alexa_block(entry_data, block, answered=True)

    assert entry_data == block


# ── A poll that suspended across teardown ─────────────────────────────────────


async def test_a_poll_in_flight_over_an_unload_does_not_revive_voice(hass: Any) -> None:
    """Cancelling the timer cannot cancel a poll already inside it, and this one
    suspends on a file read. An unloading entry is still listed, so the resumed
    poll resolves it as eligible and would rebuild the validator teardown had
    just cleared — voice answering for an integration no longer loaded."""
    from homeassistant.setup import async_setup_component

    from custom_components.selora_ai import async_unload_entry

    assert await async_setup_component(hass, "http", {})
    entry = _add(hass, _entry_data())
    _deliver(hass)
    await async_setup_entry(hass, entry)
    assert hass.data[DOMAIN]["selora_alexa_jwt_validator"] is not None

    # The poll, started and suspended at its file read.
    released = asyncio.Event()
    real_executor = hass.async_add_executor_job

    async def _slow(*args: Any, **kwargs: Any) -> Any:
        await released.wait()
        return await real_executor(*args, **kwargs)

    token = hass.data[DOMAIN]["_alexa_credential_unsub"]
    assert token is not None
    with patch.object(hass, "async_add_executor_job", _slow):
        in_flight = asyncio.ensure_future(_async_poll_alexa_credential(hass, token))
        await asyncio.sleep(0)
        # Teardown lands in the window.
        await async_unload_entry(hass, entry)
        assert hass.data[DOMAIN]["selora_alexa_jwt_validator"] is None
        released.set()
        await in_flight

    assert hass.data[DOMAIN]["selora_alexa_jwt_validator"] is None


# ── A number where a string belongs ───────────────────────────────────────────


@pytest.mark.parametrize("field", ["jwt_key", "audience", "scope", "issuer"])
async def test_a_numeric_member_is_not_a_credential(hass: Any, field: str) -> None:
    """Only `installation_id` arrives as a JSON number. Coercing the others
    turns `"jwt_key": 123` into the string "123" — a credential shaped well
    enough to replace the last good one in the cache and then raise out of
    `decode_jwt_key` on every poll, 30 seconds apart, for ever."""
    _add(hass, _entry_data())
    _deliver(hass)
    await _async_sync_alexa_runtime(hass)

    payload = json.loads(_payload_bytes())
    payload[field] = 123
    _deliver(hass, json.dumps(payload).encode() + b"\n")
    await _async_sync_alexa_runtime(hass)

    # The WHOLE credential, not just the key: a coerced audience or scope
    # builds a validator that refuses every directive while looking configured,
    # and asserting the key alone cannot tell the two apart.
    assert hass.data[DOMAIN]["selora_alexa_jwt_validator"] is not None
    assert _alexa_credentials(hass) == {
        "key": FILE_KEY_B64,
        "installation_id": str(INSTALLATION_ID),
        "issuer": ALEXA_ISSUER,
        "audience": "selora-alexa",
        "scope": "alexa:directive",
    }


async def test_a_numeric_installation_id_is_still_accepted(hass: Any) -> None:
    """It is the one that genuinely arrives as a number — Connect marshals an
    int64 — so the strictness must not reject the ordinary file."""
    _add(hass, _entry_data())
    _deliver(hass)

    await _async_sync_alexa_runtime(hass)

    assert _alexa_credentials(hass)["installation_id"] == str(INSTALLATION_ID)


# ── One resolver for both directions ──────────────────────────────────────────


async def test_a_stray_entry_cannot_split_the_installation(hass: Any) -> None:
    """`_installation_id` and `build_client` must name the same installation.
    Walked separately, a stray entry carrying only an id sorts ahead of the
    entry carrying the credential, and the config built its endpoints'
    `customIdentifier` from one installation while its client signed for
    another."""
    from custom_components.selora_ai.alexa_connect import build_client
    from custom_components.selora_ai.alexa_view import _installation_id

    _add(hass, {CONF_ENTRY_TYPE: ENTRY_TYPE_LLM, CONF_SELORA_INSTALLATION_ID: "stray-1"})
    _add(hass, _entry_alexa_data())
    await _async_sync_alexa_runtime(hass)

    client = build_client(hass, hass.data[DOMAIN])
    assert client is not None
    assert _installation_id(hass) == client._installation_id == str(INSTALLATION_ID)


async def test_a_disabled_entry_builds_no_connect_client(hass: Any) -> None:
    """The outbound walk gets the inbound rules by sharing its resolver: a
    credential the user switched off must not keep reporting state to Amazon."""
    from homeassistant.config_entries import ConfigEntryDisabler

    from custom_components.selora_ai.alexa_connect import build_client

    disabled = MockConfigEntry(
        domain=DOMAIN, data=_entry_alexa_data(), disabled_by=ConfigEntryDisabler.USER
    )
    disabled.add_to_hass(hass)
    await _async_sync_alexa_runtime(hass)

    assert build_client(hass, hass.data[DOMAIN]) is None


# ── Two acks in flight ────────────────────────────────────────────────────────


async def test_every_ack_write_stages_under_its_own_name(hass: Any) -> None:
    """Two syncs can be in flight at once — a second entry setting up while the
    poll runs. On one shared temp name the later `O_TRUNC` empties the file the
    earlier writer is about to rename into place, and Selora OS reads a garbled
    ack as no ack at all: the credential goes back in the config entry and the
    restart comes back.

    Asserted on the staging names rather than by racing writers, because a race
    that happens not to collide passes for both behaviours.
    """
    from custom_components.selora_ai import alexa_credential_file

    _add(hass, _entry_data())
    raw = _deliver(hass)
    directory = _selora_dir(hass)
    digest = hashlib.sha256(raw).hexdigest()

    staged: list[Path] = []
    real = alexa_credential_file._atomic_bytes

    def _record(tmp: Path, dest: Path, data: bytes) -> None:
        staged.append(tmp)
        real(tmp, dest, data)

    with patch.object(alexa_credential_file, "_atomic_bytes", _record):
        for value in (digest, None, digest):
            await hass.async_add_executor_job(alexa_credential_file.ensure_ack, directory, value)

    assert len(staged) == 3
    assert len({path.name for path in staged}) == 3
    assert all(path.parent == directory for path in staged)
    # Nothing left behind half-written.
    assert not list(directory.glob(".alexa_applied.json*tmp"))
