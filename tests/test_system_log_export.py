"""Tests for the redacted system_log block of the insights export."""

from __future__ import annotations

from types import SimpleNamespace

from homeassistant.components.system_log import DATA_SYSTEM_LOG
from homeassistant.core import HomeAssistant
import pytest

from custom_components.selora_ai.system_log_export import (
    MAX_ENTRIES,
    MAX_TEMPLATE_CHARS,
    attribute_domain,
    build_entries,
    collect,
    exception_type,
    export_logger,
    fingerprint,
    redact,
)

# Home Assistant core#188264, verbatim.
_LUTRON_SSL = "Reconnecting after error: check_hostname requires server_hostname"
_LUTRON_REFUSED = (
    "Reconnecting after error: [Errno 111] Connect call failed ('192.168.86.241', 8081)"
)


def _record(**overrides: object) -> dict:
    record = {
        "name": "pylutron_caseta.smartbridge",
        "message": [_LUTRON_SSL],
        "level": "WARNING",
        "source": ("runner.py", 280),
        "timestamp": 1_790_000_000.5,
        "exception": "",
        "count": 1,
        "first_occurred": 1_789_999_000.0,
    }
    record.update(overrides)
    return record


def test_lutron_messages_template_as_the_issue_reads_them() -> None:
    assert redact(_LUTRON_SSL) == _LUTRON_SSL
    assert redact(_LUTRON_REFUSED) == (
        "Reconnecting after error: [Errno <n>] Connect call failed (<str>, <n>)"
    )


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("GET https://user:pw@hub.example.com/api?x=1 failed", "GET <url> failed"),
        ("mail to owner@example.com bounced", "mail to <email> bounced"),
        ("device AA:BB:CC:DD:EE:FF gone", "device <mac> gone"),
        ("entry 3f2b8c1e-1d2a-4b5c-9e8f-0a1b2c3d4e5f missing", "entry <uuid> missing"),
        ("cannot reach 10.0.0.12:8443 now", "cannot reach <ip> now"),
        ("cannot reach fe80::1ff:fe23:4567:890a now", "cannot reach <ip> now"),
        ("bad name 'Kitchen Light' here", "bad name <str> here"),
        ('bad name "Kitchen Light" here', "bad name <str> here"),
        ("cannot open /config/secrets.yaml", "cannot open <path>"),
        ("host bridge-01.local unreachable", "host <name> unreachable"),
        ("entity light.johns_room unavailable", "entity <name> unavailable"),
        ("token abcDEF123ghiJKL456mnoPQR789 rejected", "token <token> rejected"),
        ("checksum deadbeef01 mismatch", "checksum <hex> mismatch"),
        ("retry 3 of 10 in 2.5 s", "retry <n> of <n> in <n> s"),
        ("too   many\n spaces", "too many spaces"),
    ],
)
def test_each_redaction_rule(message: str, expected: str) -> None:
    assert redact(message) == expected


def test_redaction_keeps_clock_times_and_words() -> None:
    assert redact("can't connect at 12:30") == "can't connect at <n>:<n>"
    assert redact("check_hostname_requires_server_hostname_now") == (
        "check_hostname_requires_server_hostname_now"
    )


def test_nothing_identifying_survives() -> None:
    secret = "Bearer eyJhbGciOiJIUzI1NiJ9abcdef1234567890"
    message = (
        f"auth {secret} for http://192.168.1.5/api at 192.168.1.5 "
        "host nas.home.arpa entity lock.front_door"
    )
    template = redact(message)
    for leaked in ("eyJhbGciOiJIUzI1NiJ9", "192.168", "nas.home", "front_door", "lock."):
        assert leaked not in template


def test_template_is_truncated() -> None:
    assert len(redact("x " * 500)) == MAX_TEMPLATE_CHARS


def test_redact_bounds_a_huge_message_before_matching() -> None:
    # A token cut by the bound is still long enough to be caught, and nothing
    # past the bound is examined at all.
    message = "payload " + "a1" * 1_000_000
    assert redact(message) == "payload <token>"


def test_fingerprint_is_stable_and_keyed_on_logger() -> None:
    a = fingerprint("pylutron_caseta.smartbridge", _LUTRON_SSL)
    assert a == fingerprint("pylutron_caseta.smartbridge", _LUTRON_SSL)
    assert len(a) == 16
    assert a != fingerprint("other", _LUTRON_SSL)


@pytest.mark.parametrize(
    ("exception", "expected"),
    [
        (
            "Traceback (most recent call last):\n  ...\nssl.SSLError: [SSL] bad 10.0.0.1\n",
            "ssl.SSLError",
        ),
        ("Traceback:\nTimeoutError\n", "TimeoutError"),
        ("", None),
        (None, None),
        ("Traceback:\n  something odd 'x'\n", None),
    ],
)
def test_exception_type_only(exception: str | None, expected: str | None) -> None:
    assert exception_type(exception) == expected


def test_domain_attribution() -> None:
    loggers = {"lutron_caseta": ["pylutron_caseta"], "lutron": ["pylutron"]}
    assert attribute_domain("homeassistant.components.hue.light", loggers) == "hue"
    assert attribute_domain("custom_components.selora_ai.mcp", loggers) == "selora_ai"
    assert attribute_domain("pylutron_caseta.smartbridge", loggers) == "lutron_caseta"
    assert attribute_domain("pylutron_caseta", loggers) == "lutron_caseta"
    assert attribute_domain("pylutron", loggers) == "lutron"
    assert attribute_domain("homeassistant.core", loggers) is None
    assert attribute_domain("aiohttp.client", loggers) is None


def test_longest_logger_prefix_wins() -> None:
    loggers = {"generic": ["vendor"], "specific": ["vendor.bridge"]}
    assert attribute_domain("vendor.bridge.conn", loggers) == "specific"
    assert attribute_domain("vendor.other", loggers) == "generic"


def test_entries_explode_and_dedup_messages() -> None:
    record = _record(
        message=[_LUTRON_SSL, _LUTRON_REFUSED, _LUTRON_REFUSED.replace("241", "242")],
        count=187,
    )
    entries, truncated = build_entries([record], {"lutron_caseta": ["pylutron_caseta"]})

    assert not truncated
    (entry,) = entries
    assert entry["domain"] == "lutron_caseta"
    assert entry["count"] == 187
    assert entry["first_occurred"].startswith("2026-")
    assert entry["last_occurred"].endswith("+00:00")
    assert entry["exception"] is None
    # Two raw messages differing only by IP collapse to one template.
    assert [m["template"] for m in entry["messages"]] == [
        _LUTRON_SSL,
        "Reconnecting after error: [Errno <n>] Connect call failed (<str>, <n>)",
    ]


def test_entries_are_capped_and_ordered_by_severity_then_count() -> None:
    records = [_record(name=f"lib{i}", count=i) for i in range(MAX_ENTRIES + 5)]
    records.append(_record(name="boom", level="ERROR", count=1))

    entries, truncated = build_entries(records, {})

    assert truncated
    assert len(entries) == MAX_ENTRIES
    assert entries[0]["logger"] == "boom"
    assert entries[1]["count"] == MAX_ENTRIES + 4


def test_collect_reads_the_store(hass: HomeAssistant) -> None:
    store = SimpleNamespace(to_list=lambda: [_record()])
    hass.data[DATA_SYSTEM_LOG] = SimpleNamespace(records=store)

    block = collect(hass, {})

    assert block is not None
    assert block["truncated"] is False
    assert block["entries"][0]["messages"][0]["template"] == _LUTRON_SSL


def test_collect_is_unknown_without_system_log(hass: HomeAssistant) -> None:
    hass.data.pop(DATA_SYSTEM_LOG, None)
    assert collect(hass, {}) is None


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        (
            "auth header Bearer abcdefghijklmnopqrstuvwxyz rejected",
            "auth header Bearer <secret> rejected",
        ),
        ("request api_key=secretvalue&x=1 failed", "request api_key=<secret>&x=<n> failed"),
        ("login with password: hunter two", "login with password: <secret> two"),
        ("got dGhpcyBpcyBhIHNlY3JldCB0b2tlbg+/AB== back", "got <token> back"),
        (
            "Authentication failed: invalid credentials",
            "Authentication failed: invalid credentials",
        ),
        ("Invalid token received from bridge", "Invalid token received from bridge"),
    ],
)
def test_credentials_are_redacted_by_label(message: str, expected: str) -> None:
    assert redact(message) == expected


@pytest.mark.parametrize(
    ("logger", "expected"),
    [
        ("pylutron_caseta.smartbridge", "pylutron_caseta.smartbridge"),
        (
            "homeassistant.components.automation.morning_for_alice",
            "homeassistant.components.automation",
        ),
        ("homeassistant.components.hue.light", "homeassistant.components.hue"),
        ("custom_components.foo.192.168.1.10", "custom_components.foo"),
        ("vendorlib.192.168.1.10.conn", "vendorlib.<id>.conn"),
        ("vendorlib.device.a1b2c3d4e5f6", "vendorlib.device.<id>"),
        ("vendorlib.hub-kitchen.local", "vendorlib.<id>.local"),
    ],
)
def test_logger_names_leave_the_hub_sanitised(logger: str, expected: str) -> None:
    assert export_logger(logger, {}) == expected


def test_exported_entry_carries_the_sanitised_logger_but_attributes_the_raw_one() -> None:
    entries, _ = build_entries(
        [_record(name="homeassistant.components.automation.morning_for_alice")], {}
    )
    assert entries[0]["logger"] == "homeassistant.components.automation"
    assert entries[0]["domain"] == "automation"
    assert "alice" not in str(entries)


def test_equal_logger_claims_are_ambiguous() -> None:
    loggers = {"one": ["shared"], "two": ["shared"], "three": ["shared.deep"]}
    assert attribute_domain("shared.x", loggers) is None
    assert attribute_domain("shared.deep.x", loggers) == "three"
