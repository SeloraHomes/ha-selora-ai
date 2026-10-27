"""The ``system_log`` block of the insights export: redacted, fingerprinted errors.

Selora Connect compares these across the fleet to catch an upstream integration
regression — the same integration logging the same error on every hub that runs
a given Home Assistant version — before customers report it. Home Assistant
core#188264 (Lutron Caseta reconnecting in a loop, 187 times in 16 minutes) is
the case it exists for: no single hub can tell a broken release from a bridge
that was unplugged, the fleet can.

Every level ``system_log`` holds is sent. Which levels, counts and loggers are
worth alerting on is Connect's policy, so it can change without an integration
release reaching every hub.

A message never leaves the hub as written. Log lines carry IP addresses,
hostnames, entity ids, URLs and now and then a token, so each one is reduced to
a template with those replaced by placeholders, and the template is fingerprinted
with the logger it came from. The template is what lets a person read the
alert; the fingerprint is what Connect groups on.
"""

from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import re
from typing import TYPE_CHECKING, Any, Final

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping

    from homeassistant.core import HomeAssistant

    from .types import SystemLogBlock, SystemLogEntry, SystemLogMessage

MAX_ENTRIES: Final = 30
MAX_MESSAGES: Final = 5
MAX_TEMPLATE_CHARS: Final = 240
# Redaction runs on the event loop, so a message is cut before it is matched: a
# library that logs a whole payload must not stall Home Assistant here. Long
# enough that what survives redaction still fills a template.
MAX_MESSAGE_CHARS: Final = 2000

_SEVERITY: Final = {"CRITICAL": 3, "ERROR": 2, "WARNING": 1}


def _ipv6(match: re.Match[str]) -> str:
    """Keep clock times and ratios: only something shaped like an address goes."""
    text = match.group(0)
    if "::" in text or text.count(":") >= 4 or re.search(r"[a-fA-F]", text):
        return "<ip>"
    return text


# Applied in order: each later rule sees the placeholders of the earlier ones,
# which carry no digits, dots or quotes, so nothing is replaced twice.
_Redaction = tuple[re.Pattern[str], "str | Callable[[re.Match[str]], str]"]

_CREDENTIALS: Final[tuple[_Redaction, ...]] = (
    # Credentials go by what labels them, not by their shape: a secret can be
    # short, all letters, or base64, and none of the shape rules below would
    # catch it. The label stays so the template still reads.
    # The label must END in the keyword, so prose such as "Authentication
    # failed: ..." or "token expired" keeps its words.
    (re.compile(r"(?i)\b(bearer|basic|digest)\s+\S+"), r"\1 <secret>"),
    (
        re.compile(
            r"(?i)\b([\w-]*(?:key|token|secret|passw(?:or)?d|pwd|auth|signature|session|cookie))(?![\w-])"
            r"(\s*[=:]\s*)(?!<)[^\s,;&)\]}]+"
        ),
        r"\1\2<secret>",
    ),
    # base64, which the word-character token rule cannot see across + / =.
    # Here rather than with the shapes, which would read it as a path first.
    (
        re.compile(
            r"(?<![\w+/=])(?=[A-Za-z0-9+/]*[+/])(?=[A-Za-z0-9+/]*\d)[A-Za-z0-9+/]{20,}={0,2}(?![\w+/=])"
        ),
        "<token>",
    ),
)

# Addresses can span dots and colons, so they also run over a logger name
# before it is split into segments.
_ADDRESSES: Final[tuple[_Redaction, ...]] = (
    (re.compile(r"\b[a-zA-Z][a-zA-Z0-9+.-]*://\S+"), "<url>"),
    (re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b"), "<email>"),
    (re.compile(r"\b[0-9A-Fa-f]{2}(?:[:-][0-9A-Fa-f]{2}){5}\b"), "<mac>"),
    (
        re.compile(
            r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
        ),
        "<uuid>",
    ),
    (re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}(?::\d{1,5})?\b"), "<ip>"),
    (re.compile(r"(?<![\w:])(?:[0-9a-fA-F]{0,4}:){2,7}[0-9a-fA-F]{0,4}(?![\w:])"), _ipv6),
)

_SHAPES: Final[tuple[_Redaction, ...]] = (
    (re.compile(r"(?<!\w)'[^'\n]*'(?!\w)"), "<str>"),
    (re.compile(r'"[^"\n]*"'), "<str>"),
    (re.compile(r"(?<![\w.:/<>])/[\w.\-/]+"), "<path>"),
    (re.compile(r"\b(?=[\w.-]*[A-Za-z])[\w-]+(?:\.[\w-]+)+\b"), "<name>"),
    (re.compile(r"\b(?=[\w-]*\d)(?=[\w-]*[A-Za-z])[A-Za-z0-9_-]{24,}\b"), "<token>"),
    (re.compile(r"\b(?=[0-9a-fA-F]*\d)[0-9a-fA-F]{8,}\b"), "<hex>"),
    (re.compile(r"\b\d+(?:\.\d+)?\b"), "<n>"),
)

_REDACTIONS: Final = _CREDENTIALS + _ADDRESSES + _SHAPES

_WHITESPACE: Final = re.compile(r"\s+")
_EXCEPTION_TYPE: Final = re.compile(r"^([A-Za-z_][\w.]*)(?::|$)")


def redact(message: str) -> str:
    """Reduce a log message to a template that carries nothing about the home."""
    text = message[:MAX_MESSAGE_CHARS]
    for pattern, placeholder in _REDACTIONS:
        text = pattern.sub(placeholder, text)
    text = _WHITESPACE.sub(" ", text).strip()
    return text[:MAX_TEMPLATE_CHARS]


def fingerprint(logger: str, template: str) -> str:
    return hashlib.sha256(f"{logger}\n{template}".encode()).hexdigest()[:16]


def exception_type(exception: str | None) -> str | None:
    """The exception's class from the traceback's last line, never its message."""
    lines = [line.strip() for line in (exception or "").splitlines() if line.strip()]
    if not lines:
        return None
    match = _EXCEPTION_TYPE.match(lines[-1])
    return match.group(1) if match else None


_IDENTIFIER: Final = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_OPAQUE: Final = re.compile(r"^(?=.*\d)[A-Za-z0-9_]{8,}$")


def _sanitise_segments(segments: Iterable[str]) -> list[str]:
    """Keep module-like segments; anything else, and runs of it, becomes <id>."""
    out: list[str] = []
    for segment in segments:
        keep = bool(_IDENTIFIER.match(segment)) and not _OPAQUE.match(segment)
        value = segment if keep else "<id>"
        if value == "<id>" and out and out[-1] == "<id>":
            continue
        out.append(value)
    return out


def export_logger(logger: str, loggers_by_domain: Mapping[str, Iterable[str]]) -> str:
    """The logger name as it may leave the hub.

    Logger names are not always code: Home Assistant names an automation's
    logger after the automation (``homeassistant.components.automation.<name>``)
    and integrations build loggers from hosts and device ids. Under the two
    namespaces Home Assistant owns, only the part up to the integration is
    kept; anything else keeps its module-like segments.
    """
    parts = logger.split(".")
    if len(parts) >= 3 and parts[0] == "homeassistant" and parts[1] == "components":
        return ".".join(_sanitise_segments(parts[:3]))
    if len(parts) >= 2 and parts[0] == "custom_components":
        return ".".join(_sanitise_segments(parts[:2]))
    # Hosts and addresses span segments, so they go before the split.
    text = logger
    for pattern, placeholder in _ADDRESSES:
        text = pattern.sub(placeholder, text)
    return ".".join(_sanitise_segments(text.split(".")))


def attribute_domain(logger: str, loggers_by_domain: Mapping[str, Iterable[str]]) -> str | None:
    """The integration a logger belongs to, or None when nothing claims it.

    Third-party libraries log under their own names (``pylutron_caseta``), so
    past the two namespaces Home Assistant owns, the integration manifests'
    ``loggers`` decide. A prefix matches only whole: ``pylutron`` must not claim
    ``pylutron_caseta``.
    """
    parts = logger.split(".")
    if len(parts) >= 3 and parts[0] == "homeassistant" and parts[1] == "components":
        return parts[2]
    if len(parts) >= 2 and parts[0] == "custom_components":
        return parts[1]
    best_len = -1
    owners: set[str] = set()
    for domain, prefixes in loggers_by_domain.items():
        for prefix in prefixes:
            if not (logger == prefix or logger.startswith(f"{prefix}.")):
                continue
            if len(prefix) > best_len:
                best_len, owners = len(prefix), {domain}
            elif len(prefix) == best_len:
                owners.add(domain)
    # Two integrations claiming the same prefix is a tie nothing here can
    # break, and a guess would split one regression across two domains
    # depending on the order a hub happened to load them.
    return next(iter(owners)) if len(owners) == 1 else None


def _iso(timestamp: Any) -> str | None:
    if not isinstance(timestamp, (int, float)):
        return None
    return datetime.fromtimestamp(timestamp, UTC).isoformat()


def build_entries(
    records: Iterable[Mapping[str, Any]],
    loggers_by_domain: Mapping[str, Iterable[str]],
) -> tuple[list[SystemLogEntry], bool]:
    """Shape ``DedupStore.to_list()`` records; returns (entries, truncated)."""
    entries: list[SystemLogEntry] = []
    for raw in records:
        raw_logger = str(raw.get("name") or "")
        logger = export_logger(raw_logger, loggers_by_domain)
        messages: list[SystemLogMessage] = []
        seen: set[str] = set()
        for message in raw.get("message") or []:
            template = redact(str(message))
            if not template or template in seen:
                continue
            seen.add(template)
            messages.append({"fingerprint": fingerprint(logger, template), "template": template})
            if len(messages) >= MAX_MESSAGES:
                break
        count = raw.get("count")
        entries.append(
            {
                "domain": attribute_domain(raw_logger, loggers_by_domain),
                "logger": logger,
                "level": str(raw.get("level") or "").upper(),
                "count": count if isinstance(count, int) else 1,
                "first_occurred": _iso(raw.get("first_occurred")),
                "last_occurred": _iso(raw.get("timestamp")),
                "exception": exception_type(raw.get("exception")),
                "messages": messages,
            }
        )
    entries.sort(key=lambda e: (-_SEVERITY.get(e["level"], 0), -e["count"]))
    return entries[:MAX_ENTRIES], len(entries) > MAX_ENTRIES


def collect(
    hass: HomeAssistant, loggers_by_domain: Mapping[str, Iterable[str]]
) -> SystemLogBlock | None:
    """The block for the envelope, or None when system_log cannot be read.

    None is "unknown" and the caller omits the key; an empty ``entries`` is a
    hub that has logged nothing since Core started.
    """
    try:
        from homeassistant.components.system_log import DATA_SYSTEM_LOG  # noqa: PLC0415
    except ImportError:
        return None
    store = getattr(hass.data.get(DATA_SYSTEM_LOG), "records", None)
    if store is None:
        return None
    entries, truncated = build_entries(store.to_list(), loggers_by_domain)
    return {
        "collected_at": datetime.now(UTC).isoformat(),
        "truncated": truncated,
        "entries": entries,
    }
