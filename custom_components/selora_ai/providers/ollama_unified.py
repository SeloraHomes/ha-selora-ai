"""Ollama-unified model-tag resolution for the Selora Local provider.

The ``ollama-unified`` backend serves ONE self-routing model (base + a single
merged multi-task adapter) for every intent. Its concrete Ollama tag is resolved
at runtime and NEVER hardcoded, in priority order:

  1. an explicit config override (``CONF_SELORA_LOCAL_OLLAMA_MODEL``) — used by
     benchmark runs to target a candidate tag without overwriting the shipped
     one (published tags are immutable);
  2. the best ``selora-qwen:<tag>`` the Ollama host is holding, discovered via
     ``GET /api/tags`` — ``:latest`` if it is there, else the newest version
     tag (see ``tag_sort_key``);
  3. the bare family name (Ollama resolves ``:latest``).

Kept out of the ~10k-line ``selora_local.py`` monolith: these are pure/free
functions (unit-testable without constructing the provider). The provider holds
only thin state (override + cached result) and delegates here.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

import aiohttp

from ..const import SELORA_LOCAL_OLLAMA_UNIFIED_MODEL_FAMILY

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

_LOGGER = logging.getLogger(__name__)

# One dotted chunk of a tag, ordered as ``(rank, value)`` pairs: rank 1 for a
# number, 0 for a word. The rank is what makes the key safe to compare — a
# number and a word at the same position differ in rank, so Python never has to
# compare an int with a str (which raises TypeError).
_ChunkKey = tuple[tuple[int, int | str], ...]
# A whole dotted version, most significant chunk first.
_VersionKey = tuple[_ChunkKey, ...]
# (is-latest, release, is-final-release, pre-release) — see tag_sort_key.
_SortKey = tuple[int, _VersionKey, int, _VersionKey]

# Splits a chunk into digit and non-digit runs so ``rc10`` orders ABOVE ``rc2``.
# A plain string compare puts "rc10" first, which is the wrong way round.
_TAG_RUN_RE = re.compile(r"\d+|\D+")

# Longest digit run still read as a number. Python refuses ``int()`` on a
# string past 4300 digits and raises, so an ordering key built from a
# host-supplied tag has a length at which it stops being a comparison and
# becomes an exception. Well under that, and far past any real version: a
# run this long is not a release number, so it is ranked as a word — below
# every genuine number at the same position, which is where it belongs.
_MAX_DIGIT_RUN = 18

# The tag Ollama serves for a model pulled without one, and the tag a publisher
# moves as it ships. It carries no version to compare, so it is ranked by what
# it MEANS rather than by parsing it — see tag_sort_key.
_LATEST_TAG = "latest"

# A release part with no digit in it is not a version. Tags like
# ``selora-qwen:abc`` — or the empty tag in a bare ``selora-qwen:`` — cannot be
# ordered against real versions, and answering with one addresses a model whose
# provenance nothing here knows. They are left to the family fallback; an
# operator who really wants such a tag names it in the config override.
_HAS_DIGIT_RE = re.compile(r"\d")


def _chunk_key(chunk: str) -> _ChunkKey:
    """Order one dotted chunk: digit runs numerically, word runs lexically."""
    return tuple(
        (1, int(run)) if run.isdigit() and len(run) <= _MAX_DIGIT_RUN else (0, run)
        for run in _TAG_RUN_RE.findall(chunk)
    )


def _version_key(text: str) -> _VersionKey:
    """Order a dotted version string, most significant chunk first.

    An empty string yields an empty key, which sorts below every real version —
    so a malformed tag can never win the comparison.
    """
    return tuple(_chunk_key(chunk) for chunk in text.split(".") if chunk)


def tag_sort_key(name: str) -> _SortKey:
    """Order key for a ``selora-qwen:<tag>`` Ollama tag.

    Four parts, most significant first:

      1. whether the tag is ``:latest``. It wins outright, because it is not a
         version competing with the others — it is the name the publisher moves
         to whatever is currently shipped, and the one ``ollama pull
         selora-qwen`` fetches. Version tags are the opposite: pins, kept for
         benchmarking a candidate. Ranking them above ``:latest`` means a host
         that ever pulled a candidate serves it forever, which is the failure
         part 2 exists to prevent, arriving by another door. It also agrees
         with the resolver's own last resort, which is the bare family — that
         is, ``:latest``. An operator who does want a pinned version served
         names it in the config override, which outranks all of this;
      2. the release numbers, compared most-significant-first and numerically,
         so ``0.4.10`` sorts above ``0.4.9`` rather than below it;
      3. whether the tag is a FINAL release (1) or a pre-release (0), so a
         release outranks every pre-release OF THE SAME VERSION. It does not
         demote a pre-release of a newer one: ``0.4.10-rc1`` still beats
         ``0.4.9``, exactly as semver says it should. Publishing a candidate
         under a version number no released tag has reached therefore does move
         hosts holding only version tags onto it;
      4. the pre-release parts, used only to order pre-releases against each
         other (``-rc2`` above ``-rc1``).

    Encodes no specific version: everything comes from the tag it is given.
    """
    tag = name.split(":", 1)[1] if ":" in name else name
    if tag == _LATEST_TAG:
        return (1, (), 1, ())
    release, dash, pre = tag.partition("-")
    return (0, _version_key(release), 0 if dash else 1, _version_key(pre))


def _is_addressable(name: object, prefix: str) -> bool:
    """Whether a ``/api/tags`` entry is a unified model this can serve.

    Requires the family prefix WITH its colon, which is also what keeps the
    per-specialist models out: those are named ``selora-qwen-<intent>:<tag>``,
    and a name starting ``selora-qwen-`` cannot start ``selora-qwen:``. Serving
    one of them would answer every intent with a single specialist.

    Then requires something version-shaped to order by, so a tag that carries
    no version cannot be returned as the newest one.
    """
    if not isinstance(name, str) or not name.startswith(prefix):
        return False
    tag = name.split(":", 1)[1]
    if tag == _LATEST_TAG:
        return True
    return bool(_HAS_DIGIT_RE.search(tag.partition("-")[0]))


def newest_unified_tag(tag_names: Iterable[str] | None) -> str | None:
    """The best self-routing ``selora-qwen:<tag>`` from a name list.

    ``:latest`` when the host holds it, else the newest version tag — see
    ``tag_sort_key`` for why that order. Ignores blanks, non-strings, tags with
    no version in them, and the per-specialist models; returns ``None`` when
    nothing is left, and the caller then falls back to the bare family. Ties
    break on the name so the same host always yields the same answer.
    """
    prefix = f"{SELORA_LOCAL_OLLAMA_UNIFIED_MODEL_FAMILY}:"
    unified = [name for name in (tag_names or ()) if _is_addressable(name, prefix)]
    if not unified:
        return None
    return max(unified, key=lambda name: (tag_sort_key(name), name))


def _tag_names(payload: object) -> list[object]:
    """The model names in an ``/api/tags`` body, whatever shape it arrived in.

    The envelope is shape-checked before anything is read out of it, not just
    the fields inside — the same rule the ``/v1/models`` parse on the other
    backend follows, and for the same reason. ``host`` is user-configured, so
    a proxy or an unrelated server on that port can answer 200 with a JSON
    array, a scalar, or a ``models`` list of strings, and reading ``.get`` off
    one of those raises out of a resolver whose callers do not catch it: the
    panel's config load and every chat turn on this backend go through it.

    Entries are returned as they were found. Deciding which of them is a name
    worth serving is ``newest_unified_tag``'s job, and it already ignores
    blanks and non-strings.
    """
    if not isinstance(payload, dict):
        return []
    models = payload.get("models")
    if not isinstance(models, list):
        return []
    return [entry.get("name") for entry in models if isinstance(entry, dict)]


async def resolve_unified_model(
    session: aiohttp.ClientSession,
    host: str,
    *,
    headers: Mapping[str, str] | None = None,
    timeout: aiohttp.ClientTimeout | None = None,
) -> str:
    """Resolve the ollama-unified model tag from the Ollama host.

    Queries ``GET /api/tags`` and returns the best ``selora-qwen:<tag>`` the
    host is holding, falling back to the bare family (``:latest``) when the host
    is unreachable or has no usable tag. Callers that hold an explicit override
    should use it directly and not call this.
    """
    try:
        async with session.get(f"{host}/api/tags", headers=headers, timeout=timeout) as resp:
            if resp.status == 200:
                newest = newest_unified_tag(_tag_names(await resp.json()))
                if newest is not None:
                    return newest
    except (aiohttp.ClientError, TimeoutError, ValueError) as exc:
        # ValueError covers a 200 whose body is not JSON at all: ``host`` is
        # user-configured, so whatever answers on that port may be a proxy, a
        # captive portal, or an unrelated service, and none of them owe us a
        # decodable body.
        _LOGGER.debug("Selora Local /api/tags probe failed: %s", exc)
    return SELORA_LOCAL_OLLAMA_UNIFIED_MODEL_FAMILY
