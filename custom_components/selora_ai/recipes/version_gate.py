"""Minimum-integration-version gate for recipes.

A recipe manifest may declare ``min_integration_version`` — the lowest
Selora AI integration version that ships the features the recipe relies
on (e.g. integration-scoped role filtering, the ``event`` role kind).
Recipes requiring a newer integration than the one installed are hidden
from the catalog so a homeowner on an older version never installs a
recipe that would silently misbehave.

The comparison is deliberately lenient:

- Only the leading ``major.minor.patch`` release components are compared.
  Prerelease / build suffixes (``0.12.0b3``, ``0.12.0-pre.4``) are
  ignored: a 0.12.0 beta already carries the 0.12.0 feature set, so a
  beta tester should see recipes that require 0.12.0.
- A missing / blank / unparseable ``minimum`` means "no requirement" →
  the recipe is shown. Authors opt in by declaring the field.
- An unparseable installed version (a corrupt ``manifest.json`` read
  returning ``""``) also shows the recipe — a failed version read must
  never blank out the whole catalog.
"""

from __future__ import annotations

from functools import lru_cache
import json
import logging
from pathlib import Path
import re

_LOGGER = logging.getLogger(__name__)

# Leading dotted-numeric release, e.g. "0.12.0" out of "v0.12.0",
# "0.12.0b3", "0.12.0-pre.4". Stops at the first non-"digit-or-dot" run.
_RELEASE_RE = re.compile(r"\s*v?(\d+(?:\.\d+)*)")


def _release_tuple(version: str) -> tuple[int, ...] | None:
    """Parse the leading ``major.minor.patch`` of a version string into a
    tuple of ints, or ``None`` when there's nothing numeric to compare.
    """
    match = _RELEASE_RE.match(version or "")
    if not match:
        return None
    return tuple(int(part) for part in match.group(1).split("."))


def meets_minimum(current: str, minimum: str) -> bool:
    """Return ``True`` when ``current`` satisfies the ``minimum`` floor.

    See the module docstring for the leniency rules — blank/unparseable
    bounds and blank/unparseable installed versions both return ``True``.
    """
    if not (minimum or "").strip():
        return True
    min_tuple = _release_tuple(minimum)
    if min_tuple is None:
        return True
    cur_tuple = _release_tuple(current)
    if cur_tuple is None:
        return True
    width = max(len(cur_tuple), len(min_tuple))
    cur_padded = cur_tuple + (0,) * (width - len(cur_tuple))
    min_padded = min_tuple + (0,) * (width - len(min_tuple))
    return cur_padded >= min_padded


def is_newer(candidate: str, installed: str) -> bool:
    """Return ``True`` when ``candidate`` is a later release than ``installed``.

    Same parsing as :func:`meets_minimum`, but strict and the other way
    round on bad input: an unparseable version on either side is never an
    update, because offering one that may be a downgrade rewrites a
    working install for nothing.
    """
    cand_tuple = _release_tuple(candidate)
    inst_tuple = _release_tuple(installed)
    if cand_tuple is None or inst_tuple is None:
        return False
    width = max(len(cand_tuple), len(inst_tuple))
    return cand_tuple + (0,) * (width - len(cand_tuple)) > inst_tuple + (0,) * (
        width - len(inst_tuple)
    )


def is_same_release(version: str, other: str) -> bool:
    """Return ``True`` when both name the same release: ``v2.1`` is ``2.1.0``.

    Strict like :func:`is_newer`: an unparseable version matches nothing, and
    a suffix (``-rc1``) must be the same on both.
    """
    parsed = [(_RELEASE_RE.match(v or ""), v or "") for v in (version, other)]
    if any(match is None for match, _ in parsed):
        return False
    tuples = [tuple(int(p) for p in match.group(1).split(".")) for match, _ in parsed if match]
    width = max(len(t) for t in tuples)
    padded = {t + (0,) * (width - len(t)) for t in tuples}
    suffixes = {text[match.end() :].strip() for match, text in parsed if match}
    return len(padded) == 1 and len(suffixes) == 1


@lru_cache(maxsize=1)
def integration_version() -> str:
    """Read the integration version from ``manifest.json`` (cached).

    Returns ``""`` when the manifest can't be read or parsed — callers
    treat that as "no floor can be enforced" and show every recipe.
    Blocking file I/O: call once via ``async_add_executor_job`` from the
    event loop; the ``lru_cache`` makes every later call free.
    """
    try:
        manifest = Path(__file__).resolve().parent.parent / "manifest.json"
        with manifest.open(encoding="utf-8") as handle:
            return str(json.load(handle).get("version", ""))
    except (OSError, ValueError):
        _LOGGER.debug("Could not read integration version from manifest.json", exc_info=True)
        return ""
