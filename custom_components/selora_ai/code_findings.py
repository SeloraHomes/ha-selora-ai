"""What a browser-code file does that a reviewer should see before trusting it.

Facts for the person approving an agent's file, not a verdict: a pattern
search cannot prove code safe, and the agent that wrote the file must not be
able to argue its way past the check — so this is deterministic, never a model.
Each finding names a kind the panel translates, a severity, the code excerpt and
its line. A finding repeated in a file is reported once, with its count.
"""

from __future__ import annotations

from collections.abc import Iterable
import re
from typing import Final, Literal, NotRequired, TypedDict

from .helpers import sanitize_untrusted_text

Severity = Literal["high", "medium", "info"]
Kind = Literal[
    "network",
    "external_url",
    "credentials",
    "dynamic_code",
    "admin_api",
    "service",
    "obfuscation",
]


class Finding(TypedDict):
    """One thing a file does, for the reviewer."""

    kind: Kind
    severity: Severity
    detail: str
    line: int | None
    # Found in a version since replaced or deleted (``FileAccessManager``).
    earlier: NotRequired[bool]


_SEVERITY_ORDER: Final[dict[str, int]] = {"high": 0, "medium": 1, "info": 2}
_MAX_FINDINGS: Final = 25
_MAX_DETAIL: Final = 80
_MINIFIED_LINE: Final = 2000

# (kind, severity, pattern). The excerpt shown is the match itself.
_PATTERNS: Final[tuple[tuple[Kind, Severity, re.Pattern[str]], ...]] = (
    # Sends or loads something over the network: the way data leaves the home.
    ("network", "high", re.compile(r"\bfetch\s*\(")),
    ("network", "high", re.compile(r"\bXMLHttpRequest\b")),
    ("network", "high", re.compile(r"\bnew\s+WebSocket\b")),
    ("network", "high", re.compile(r"\bEventSource\b")),
    ("network", "high", re.compile(r"\bsendBeacon\b")),
    ("network", "high", re.compile(r"\bRTCPeerConnection\b")),
    ("network", "high", re.compile(r"\bimportScripts\b")),
    ("network", "high", re.compile(r"\bimport\s*\(")),
    ("network", "high", re.compile(r"\bserviceWorker\b")),
    # Reads the session: whoever has the token is the user.
    ("credentials", "high", re.compile(r"\bhassTokens\b")),
    ("credentials", "high", re.compile(r"\blocalStorage\b")),
    ("credentials", "high", re.compile(r"\bsessionStorage\b")),
    ("credentials", "high", re.compile(r"\bdocument\s*\.\s*cookie\b")),
    ("credentials", "high", re.compile(r"\bindexedDB\b")),
    ("credentials", "high", re.compile(r"\bhass\s*\.\s*auth\b")),
    ("credentials", "high", re.compile(r"\.auth\s*\.\s*data\b")),
    ("credentials", "high", re.compile(r"\b(?:access|refresh)_?[tT]oken\b")),
    ("credentials", "high", re.compile(r"\bconnection\s*\.\s*options\b")),
    # Turns text into code, which hides what will run from this very check.
    ("dynamic_code", "high", re.compile(r"\beval\s*\(")),
    ("dynamic_code", "high", re.compile(r"\bnew\s+Function\b")),
    ("dynamic_code", "high", re.compile(r"\bFunction\s*\(")),
    ("dynamic_code", "high", re.compile(r"\bset(?:Timeout|Interval)\s*\(\s*['\"`]")),
    ("dynamic_code", "high", re.compile(r"<script\b", re.IGNORECASE)),
    ("dynamic_code", "medium", re.compile(r"\bdocument\s*\.\s*write\b")),
    ("dynamic_code", "medium", re.compile(r"\.innerHTML\s*=(?!=)")),
    ("dynamic_code", "medium", re.compile(r"\binsertAdjacentHTML\b")),
    ("dynamic_code", "medium", re.compile(r"\bcreateContextualFragment\b")),
    # Commands beyond calling a service: registries, config, users, restarts.
    ("admin_api", "medium", re.compile(r"\bcallWS\s*\(")),
    ("admin_api", "medium", re.compile(r"\bsendMessage(?:Promise)?\s*\(")),
    ("admin_api", "medium", re.compile(r"\bsubscribeMessage\s*\(")),
    ("admin_api", "medium", re.compile(r"\bcallApi\s*\(")),
    ("admin_api", "medium", re.compile(r"['\"`]/api/")),
    # Hides what the code says.
    ("obfuscation", "medium", re.compile(r"\batob\s*\(")),
    ("obfuscation", "medium", re.compile(r"\bString\s*\.\s*fromCharCode\b")),
    ("obfuscation", "medium", re.compile(r"(?:\\x[0-9a-fA-F]{2}){8,}")),
    ("obfuscation", "medium", re.compile(r"(?:\\u[0-9a-fA-F]{4}){8,}")),
    ("obfuscation", "medium", re.compile(r"['\"`][A-Za-z0-9+/]{200,}={0,2}['\"`]")),
)

# A scheme-less ``//host`` only inside a string or ``url(``: bare, it is a
# comment (``//this.value``), and reading it as a host flags every file.
_URL_RE: Final = re.compile(r"""(?:https?:|(?<=['"`(]))//([A-Za-z0-9.-]+\.[A-Za-z]{2,})(?::\d+)?""")
# Namespace URIs SVG and XML code names; nothing is fetched from them.
_NAMESPACE_HOSTS: Final = frozenset({"www.w3.org"})

_SERVICE_RE: Final = re.compile(
    r"""\bcallService\s*\(\s*(?:['"`]([a-z0-9_]+)['"`]\s*,\s*['"`]([a-z0-9_]+)['"`])?"""
)
# Domains whose services open, unlock, disarm, restart or reach outside —
# the ones a dashboard button pressing them deserves a second look for.
_SENSITIVE_DOMAINS: Final = frozenset(
    {
        "alarm_control_panel",
        "automation",
        "backup",
        "cover",
        "hassio",
        "homeassistant",
        "lock",
        "notify",
        "person",
        "recorder",
        "rest_command",
        "script",
        "shell_command",
        "siren",
        "update",
        "valve",
    }
)


# A method or function definition, for the helper a dynamic call sits in.
_DEFINITION_RE: Final = re.compile(
    r"""^\s*(?:(?:async|static)\s+)*(?:function\s*\*?\s*)?([A-Za-z_$][\w$]*)\s*"""
    r"""(?:=\s*(?:async\s*)?)?\(([^()]*)\)\s*(?:=>\s*)?\{"""
)
_KEYWORDS: Final = frozenset({"if", "for", "while", "switch", "catch", "with", "return"})
_QUOTED_WORD_RE: Final = re.compile(r"""(['"`])([a-z][a-z0-9_]*)\1""")
_RUNTIME_UNLISTED: Final = "which actions is decided while it runs; none could be listed"
_RUNTIME_MORE: Final = "may also call actions decided while it runs"


def _action_severity(domain: str) -> Severity:
    return "medium" if domain in _SENSITIVE_DOMAINS else "info"


def _dispatchers(lines: list[str], call_lines: Iterable[int]) -> set[str]:
    """The helpers whose bodies call a service by variable (``_call``)."""
    names = set()
    for index in call_lines:
        for back in range(index, max(index - 40, -1), -1):
            match = _DEFINITION_RE.match(lines[back])
            if match and match.group(1) not in _KEYWORDS:
                names.add(match.group(1))
                break
    return names


def _named_actions(text: str, call_lines: Iterable[int]) -> Iterable[tuple[int, str, str]]:
    """(line, domain, service) for each call to a helper that calls by variable.

    A card that routes every press through one helper (``_call(domain,
    service, data)``) still names its actions where it calls the helper: the
    list a reviewer wants. A service that is itself a variable there is
    reported as the domain's any action.
    """
    lines = text.splitlines()
    names = _dispatchers(lines, call_lines)
    if not names:
        return
    call_re = re.compile(
        r"\b(?:"
        + "|".join(re.escape(n) for n in sorted(names))
        + r""")\s*\(\s*(['"`])([a-z][a-z0-9_]*)\1\s*,\s*([^,)]*)"""
    )
    for number, line in enumerate(lines, start=1):
        if len(line) > _MINIFIED_LINE:
            continue
        for match in call_re.finditer(line):
            domain = match.group(2)
            services = [m.group(2) for m in _QUOTED_WORD_RE.finditer(match.group(3))]
            for service in services or ["*"]:
                yield number, domain, service


def _excerpt(text: str) -> str:
    return sanitize_untrusted_text(text.strip(), _MAX_DETAIL)


def _line_findings(line: str) -> Iterable[tuple[Kind, Severity, str]]:
    for kind, severity, pattern in _PATTERNS:
        for match in pattern.finditer(line):
            yield kind, severity, match.group(0)
    for match in _URL_RE.finditer(line):
        host = match.group(1).lower()
        if host not in _NAMESPACE_HOSTS:
            yield "external_url", "high", host
    for match in _SERVICE_RE.finditer(line):
        domain, service = match.group(1), match.group(2)
        if domain is None:
            yield "service", "medium", _RUNTIME_UNLISTED
        else:
            yield "service", _action_severity(domain), f"{domain}.{service}"
    if len(line) > _MINIFIED_LINE:
        yield "obfuscation", "info", f"minified line ({len(line)} characters)"


def analyze(text: str) -> list[Finding]:
    """The findings for one file, most severe first, each reported once."""
    first: dict[tuple[str, str], Finding] = {}
    counts: dict[tuple[str, str], int] = {}
    runtime_lines: list[int] = []
    for number, line in enumerate(text.splitlines(), start=1):
        for kind, severity, raw in _line_findings(line):
            if raw == _RUNTIME_UNLISTED:
                runtime_lines.append(number - 1)
            detail = _excerpt(raw)
            key = (kind, detail)
            counts[key] = counts.get(key, 0) + 1
            if key not in first:
                first[key] = Finding(kind=kind, severity=severity, detail=detail, line=number)
    runtime = first.get(("service", _RUNTIME_UNLISTED))
    if runtime is not None:
        for number, domain, service in _named_actions(text, runtime_lines):
            action = f"{domain} (any action)" if service == "*" else f"{domain}.{service}"
            key = ("service", action)
            if key not in first:
                first[key] = Finding(
                    kind="service",
                    severity=_action_severity(domain),
                    detail=key[1],
                    line=number,
                )
                counts[key] = 1
        if len([k for k in first if k[0] == "service"]) > 1:
            # The actions are listed; the dispatcher itself is a footnote.
            del first[("service", _RUNTIME_UNLISTED)]
            first[("service", _RUNTIME_MORE)] = Finding(
                kind="service", severity="info", detail=_RUNTIME_MORE, line=runtime["line"]
            )
            counts[("service", _RUNTIME_MORE)] = 1
    findings = []
    for key, finding in first.items():
        if counts[key] > 1:
            finding["detail"] = f"{finding['detail']} (×{counts[key]})"
        findings.append(finding)
    findings.sort(key=lambda f: (_SEVERITY_ORDER[f["severity"]], f["line"] or 0))
    return findings[:_MAX_FINDINGS]


def summarize(findings: Iterable[Finding]) -> str:
    """'2 high, 1 medium' — or 'no risky patterns' — for a tool result."""
    tally: dict[str, int] = {}
    for finding in findings:
        if finding["severity"] != "info":
            tally[finding["severity"]] = tally.get(finding["severity"], 0) + 1
    if not tally:
        return "no risky patterns"
    return ", ".join(f"{tally[s]} {s}" for s in ("high", "medium") if s in tally)
