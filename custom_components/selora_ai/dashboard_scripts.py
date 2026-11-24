"""JavaScript carried inside dashboard card config.

Several custom cards evaluate strings from their own config as JavaScript in
the browser of whoever opens the dashboard, with that user's Home Assistant
session: button-card and streamline-card ``[[[ … ]]]`` templates (and
streamline-card's ``*_javascript`` keys), config-template-card ``${ … }``
expressions, apexcharts-card ``EVAL:`` functions. A ``javascript:`` URL in a
tap action or an iframe card runs the same way. Writing one is writing browser
code, which Selora never does (``docs/dev/config-files.md``) — so a dashboard
write may not INTRODUCE one. Code already stored came from the user and may be
kept, moved and grouped; changing it is introducing new code.

Markers, not card types: the cards that evaluate are custom, so a list of
types can never be complete, while the markers are the cards' own syntax and
mean nothing to the cards that do not evaluate them.
"""

from __future__ import annotations

from collections.abc import Iterable
import re
from typing import Any, Final

from .helpers import sanitize_untrusted_text

_JS_TEMPLATE_RE: Final = re.compile(r"\[\[\[.*?\]\]\]", re.DOTALL)
_JS_EXPRESSION: Final = "${"
_JS_EVAL_PREFIX: Final = "EVAL:"
_JS_KEY_SUFFIX: Final = "_javascript"
_SCRIPT_SCHEMES: Final = ("javascript:", "vbscript:")

# A browser drops tabs and newlines anywhere in a URL and trims leading C0
# controls and spaces, so "java\tscript:" and " javascript:" both run.
_URL_IGNORED: Final = dict.fromkeys(map(ord, "\t\n\r"))

_MAX_SHOWN: Final = 80


def _is_script(value: str, key: str | None) -> bool:
    if key is not None and key.endswith(_JS_KEY_SUFFIX):
        return True
    if _JS_TEMPLATE_RE.search(value) or _JS_EXPRESSION in value:
        return True
    if value.lstrip().startswith(_JS_EVAL_PREFIX):
        return True
    url = value.translate(_URL_IGNORED).lstrip("".join(map(chr, range(0x21)))).lower()
    return url.startswith(_SCRIPT_SCHEMES)


def script_strings(node: Any) -> set[str]:
    """Every string in a dashboard document, card or view that a browser runs."""
    found: set[str] = set()
    stack: list[tuple[Any, str | None]] = [(node, None)]
    while stack:
        current, key = stack.pop()
        if isinstance(current, dict):
            stack.extend((v, k if isinstance(k, str) else None) for k, v in current.items())
        elif isinstance(current, list):
            stack.extend((item, key) for item in current)
        elif isinstance(current, str) and _is_script(current, key):
            found.add(current)
    return found


def introduced_script_error(document: Any, baselines: Iterable[Any]) -> str | None:
    """Refusal for a write that adds browser code none of ``baselines`` held."""
    kept: set[str] = set()
    for baseline in baselines:
        kept |= script_strings(baseline)
    added = script_strings(document) - kept
    if not added:
        return None
    sample = sanitize_untrusted_text(min(added, key=len), _MAX_SHOWN)
    return (
        "Not saved: the card config holds JavaScript that runs in the browser of "
        f"everyone who opens the dashboard (e.g. {sample!r}). Selora does not write "
        "code that runs in the browser — button-card [[[ ]]] templates, ${ } "
        "expressions, EVAL: functions, *_javascript keys or javascript: URLs. Use the "
        "card's plain options, or a Jinja template where the card takes one (markdown "
        "content, card-mod). JavaScript the dashboard already holds can be kept or "
        "moved, but not changed."
    )
