"""Ordering rules for the ollama-unified model tag.

Server-free: these exercise the pure ordering functions, not ``GET /api/tags``,
so they run in the normal unit suite with nothing serving.

No test spells out a shipped version. Tags are built from the family constant
plus deliberately fictional version numbers, because a real version written into
a test is the same conflict hazard as one written into the source.
"""

from __future__ import annotations

import ast
from pathlib import Path
import re
import subprocess

import pytest

from custom_components.selora_ai import const
from custom_components.selora_ai.const import SELORA_LOCAL_OLLAMA_UNIFIED_MODEL_FAMILY
from custom_components.selora_ai.providers import ollama_unified, selora_local
from custom_components.selora_ai.providers.ollama_unified import (
    newest_unified_tag,
    tag_sort_key,
)

FAM = SELORA_LOCAL_OLLAMA_UNIFIED_MODEL_FAMILY


def tag(version: str) -> str:
    """A unified tag for a fictional version, e.g. ``selora-qwen:9.9.9``."""
    return f"{FAM}:{version}"


def specialist(intent: str, version: str) -> str:
    """One of the older per-intent models, e.g. ``selora-qwen-command:9.9.9``."""
    return f"{FAM}-{intent}:{version}"


# ── the bug this file exists for ─────────────────────────────────────


def test_release_beats_its_release_candidate() -> None:
    """A finished release must win over an RC of the same version.

    The old key appended the ``rc`` part to the release parts, which made the RC
    a strictly longer tuple and therefore the larger one — so a host holding both
    served the RC to users.
    """
    assert newest_unified_tag([tag("9.9.9"), tag("9.9.9-rc")]) == tag("9.9.9")


def test_release_beats_its_release_candidate_either_input_order() -> None:
    """Order of the /api/tags response must not change the answer."""
    assert newest_unified_tag([tag("9.9.9-rc"), tag("9.9.9")]) == tag("9.9.9")


def test_release_beats_a_numbered_release_candidate() -> None:
    assert newest_unified_tag([tag("9.9.9-rc2"), tag("9.9.9")]) == tag("9.9.9")


# ── ordinary version ordering ────────────────────────────────────────


def test_newest_release_wins() -> None:
    assert newest_unified_tag([tag("9.8.9"), tag("9.9.9"), tag("9.9.2")]) == tag("9.9.9")


def test_double_digit_patch_beats_single_digit() -> None:
    """String ordering would put 9.9.9 above 9.9.10. Numeric ordering must not."""
    assert newest_unified_tag([tag("9.9.9"), tag("9.9.10")]) == tag("9.9.10")


def test_double_digit_minor_beats_single_digit() -> None:
    assert newest_unified_tag([tag("9.9.0"), tag("9.10.0")]) == tag("9.10.0")


def test_longer_version_beats_its_own_prefix() -> None:
    assert newest_unified_tag([tag("9.9"), tag("9.9.1")]) == tag("9.9.1")


def test_release_candidates_order_numerically_among_themselves() -> None:
    """rc10 is newer than rc2, even though it sorts first as a string."""
    assert newest_unified_tag([tag("9.9.9-rc2"), tag("9.9.9-rc10")]) == tag("9.9.9-rc10")


def test_release_candidate_of_a_newer_version_beats_an_older_release() -> None:
    """Precedence is per version: the RC rule does not demote a newer version."""
    assert newest_unified_tag([tag("9.9.9"), tag("9.9.10-rc1")]) == tag("9.9.10-rc1")


def test_latest_beats_a_numbered_tag() -> None:
    """``:latest`` is the tag a publisher moves to whatever is shipped, and
    the one a plain ``ollama pull`` fetches. A version tag is a pin someone
    kept — usually to benchmark a candidate — so ranking pins above it means
    a host that pulled one serves it forever. This is also the answer the
    resolver falls back to when it finds nothing, so the two agree."""
    assert newest_unified_tag([tag("latest"), tag("9.9.9")]) == tag("latest")


def test_latest_wins_from_either_input_order() -> None:
    assert newest_unified_tag([tag("9.9.9"), tag("latest")]) == tag("latest")


def test_latest_beats_a_release_candidate_too() -> None:
    assert newest_unified_tag([tag("9.9.10-rc1"), tag("latest")]) == tag("latest")


def test_a_version_tag_still_wins_when_latest_is_absent() -> None:
    """Nothing here requires ``:latest`` to be present — a host that only
    holds pins still gets the newest of them."""
    assert newest_unified_tag([tag("9.9.9"), tag("9.9.10")]) == tag("9.9.10")


def test_latest_of_another_family_is_still_ignored() -> None:
    assert newest_unified_tag([f"{FAM}-command:latest", "llama4:latest"]) is None


# ── what must be ignored ─────────────────────────────────────────────


def test_per_specialist_models_are_not_unified_models() -> None:
    """The llama.cpp-style per-specialist tags serve one intent each."""
    names = [specialist("command", "9.9.9"), tag("9.9.1")]
    assert newest_unified_tag(names) == tag("9.9.1")


def test_other_families_are_ignored() -> None:
    assert newest_unified_tag(["llama4:latest", "some-other-model:1.0"]) is None


def test_empty_input_returns_none() -> None:
    assert newest_unified_tag([]) is None


def test_none_input_returns_none() -> None:
    assert newest_unified_tag(None) is None


def test_only_per_specialist_models_returns_none() -> None:
    assert newest_unified_tag([specialist("answer", "9.9.9")]) is None


# ── malformed input must not crash the chat path ─────────────────────


@pytest.mark.parametrize(
    "names",
    [
        [tag(""), tag("9.9.9")],
        [tag("..."), tag("9.9.9")],
        [tag("9.9.9"), tag("not-a-version")],
        [tag("9.9.9"), tag("9.9.9-")],
        [tag("9.9.9"), tag("-rc")],
    ],
)
def test_garbage_tags_never_beat_a_real_version(names: list[str]) -> None:
    """Mixing words and numbers must not raise, and must not win."""
    assert newest_unified_tag(names) == tag("9.9.9")


@pytest.mark.parametrize("version", ["", "abc", "-rc1", "..."])
def test_a_tag_with_no_version_in_it_is_not_an_answer(version: str) -> None:
    """``selora-qwen:`` and ``selora-qwen:abc`` are names the provider would
    then address, with nothing here able to say what they hold. Falling
    through to the family is the honest answer; an operator who means such a
    tag names it in the config override."""
    assert newest_unified_tag([tag(version)]) is None


def test_a_tag_with_no_version_never_outranks_one_that_has_it() -> None:
    assert newest_unified_tag([tag("abc"), tag("9.9.9")]) == tag("9.9.9")


def test_non_string_entries_are_skipped() -> None:
    assert newest_unified_tag([None, 7, {}, tag("9.9.9")]) == tag("9.9.9")  # type: ignore[list-item]


def test_bare_family_without_a_tag_is_not_selected() -> None:
    """Every /api/tags name carries a tag; a bare family name is not a model."""
    assert newest_unified_tag([FAM]) is None


def test_result_is_stable_for_duplicate_entries() -> None:
    assert newest_unified_tag([tag("9.9.9"), tag("9.9.9")]) == tag("9.9.9")


# ── the key itself ───────────────────────────────────────────────────


def test_sort_key_orders_a_full_list_oldest_to_newest() -> None:
    ordered = [tag("9.8.9"), tag("9.9.9-rc1"), tag("9.9.9-rc2"), tag("9.9.9"), tag("9.9.10")]
    assert sorted(ordered, key=tag_sort_key) == ordered


# ── the standing rule: nothing here knows a version ──────────────────
#
# The old guard read one file, so a version written into the constants
# next door — or into any other file of the same change — went through
# untouched. These read every module the model name is built from, plus
# whatever else the branch touched.


def _code_strings(path: Path) -> list[tuple[int, str]]:
    """Every string literal in a module that is not a docstring.

    Parsing rather than reading lines: comments disappear on their own,
    docstrings are identified rather than guessed at, and a version named
    in prose stays allowed while one named in code does not.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docs = {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef)
        and ast.get_docstring(node) is not None
    }
    return [
        (node.lineno, node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docs
    ]


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _changed_python_files() -> list[Path]:
    """The branch's own Python files, or [] when the base is unreadable.

    A HACS checkout has no git history and CI may clone shallow, so an
    unanswerable question is answered with nothing rather than a failure —
    the module list below is what the guard always covers.
    """
    root = _repo_root()
    try:
        base = subprocess.run(
            ["git", "merge-base", "HEAD", "origin/main"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        ).stdout.strip()
        names = subprocess.run(
            ["git", "diff", "--name-only", base, "HEAD"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        ).stdout.split()
    except (OSError, subprocess.SubprocessError):
        return []
    return [p for name in names if name.endswith(".py") and (p := root / name).is_file()]


# Where the model name is assembled. Covered whether or not the branch
# touched them, so the guard cannot lapse into a no-op.
_TAG_PATH_MODULES = (const, ollama_unified, selora_local)

# A version literal is a quoted, dotted number such as "0.4.9".
_VERSION_RE = re.compile(r"\d+\.\d+")


def test_the_tag_path_names_no_version() -> None:
    """The resolver and the provider it serves must not know a version:
    one written here is one every install is pinned to, which is the
    reason the tag is discovered at runtime at all."""
    for module in (ollama_unified, selora_local):
        for lineno, value in _code_strings(Path(module.__file__)):
            assert not _VERSION_RE.search(value), f"{module.__name__}:{lineno} {value!r}"


def test_no_selora_local_constant_names_a_version() -> None:
    """Read by name rather than by scanning the file: const.py also holds
    the cloud providers' model ids, which are versioned on purpose."""
    for name, value in vars(const).items():
        if isinstance(value, str) and "SELORA_LOCAL" in name:
            assert not _VERSION_RE.search(value), name


def test_the_model_family_is_a_family_and_not_a_tag() -> None:
    """Everything downstream builds names by appending to this. A tag or a
    digit in it is a pinned model reached through every one of them."""
    assert ":" not in FAM
    assert not any(char.isdigit() for char in FAM)


def test_nothing_in_this_change_writes_out_a_model_tag() -> None:
    """Tags are built from the family constant, never spelled out — that
    is what lets tests use invented versions while no shipped one is
    written down anywhere. Covers every file the branch touched, tests
    included, since a tag pinned in a fixture is a tag pinned."""
    written_tag = f"{FAM}:"
    for path in {*_changed_python_files(), *(Path(m.__file__) for m in _TAG_PATH_MODULES)}:
        for lineno, value in _code_strings(path):
            assert written_tag not in value, f"{path.name}:{lineno} {value!r}"
