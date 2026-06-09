#!/usr/bin/env python3
"""Prepare a patch release on a maintenance branch.

semantic-release cannot ship `0.9.1` while main's latest tag is `0.9.0`
and main has queued `feat:` commits — its maintenance-branch range
collapses to empty. This script does the equivalent prep work locally:

1. Verifies we're on an `N.N.x` branch with a clean working tree.
2. Verifies the requested version is a valid patch bump from the
   manifest's current version.
3. Generates a CHANGELOG section from conventional commits since the
   last tag reachable on this branch.
4. Bumps `custom_components/selora_ai/manifest.json`.
5. Rebuilds the frontend bundle so its embedded version matches.
6. Prepends the new section to `CHANGELOG.md`.
7. Commits `chore(release): X.Y.Z [skip ci]` and tags `vX.Y.Z` locally.

Pushing + GitHub mirror + GitLab release page are handled by
`release_publish.sh` so the user can review the diff in between.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
MANIFEST_PATH = REPO_ROOT / "custom_components" / "selora_ai" / "manifest.json"
CHANGELOG_PATH = REPO_ROOT / "CHANGELOG.md"
FRONTEND_DIR = REPO_ROOT / "custom_components" / "selora_ai" / "frontend"
COMPARE_URL = "https://gitlab.com/selorahomes/products/selora-ai/ha-integration"
MAINTENANCE_BRANCH_RE = re.compile(r"^\d+\.\d+\.x$")
SEMVER_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
CONVENTIONAL_RE = re.compile(
    r"^(?P<type>feat|fix|perf|refactor|revert|docs|chore|style|test|build|ci)"
    r"(?:\((?P<scope>[^)]+)\))?(?P<breaking>!)?:\s*(?P<subject>.+)$"
)
# `git revert <sha>` produces "Revert \"<original subject>\"" — not a
# conventional header but @semantic-release/commit-analyzer treats it
# as a patch-producing revert. Without this fallback, a revert-only
# range would be rejected as "nothing to release" and a mixed range
# would silently omit the revert from the changelog.
REVERT_SUBJECT_RE = re.compile(r'^Revert\s+"(?P<inner>.+)"\s*$')

# Section order matches release.config.js's release-notes-generator preset.
SECTION_ORDER = [
    ("feat", "Features"),
    ("fix", "Bug Fixes"),
    ("perf", "Performance"),
    ("refactor", "Code Refactoring"),
    ("revert", "Reverts"),
]
# Types that actually trigger a release per release.config.js. refactor
# maps to `release: false` there, so a range of only-refactors must NOT
# produce a patch — semantic-release would emit no release on the same
# commits, and the manual flow must not diverge.
PATCH_PRODUCING_TYPES = {"fix", "perf", "revert"}


def run(cmd: list[str], check: bool = True, capture: bool = True) -> str:
    """Run a shell command, return stdout (stripped)."""
    result = subprocess.run(
        cmd,
        cwd=REPO_ROOT,
        check=check,
        capture_output=capture,
        text=True,
    )
    return result.stdout.strip()


def fail(msg: str) -> None:
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(1)


def verify_clean_tree() -> None:
    status = run(["git", "status", "--porcelain"])
    if status:
        fail(
            "working tree is not clean — commit or stash changes first:\n" + status
        )


def verify_tag_absent(version: str) -> None:
    """Refuse to start if `vX.Y.Z` already exists locally OR on origin.

    `git_commit_and_tag` commits first then tags. If the tag exists,
    the commit succeeds and `git tag` then fails, leaving a release
    commit on the branch with the bumped manifest but no matching
    tag — a half-state that the patch-bump guard would then misread
    on retry ("manifest already at X.Y.Z, can't bump to X.Y.Z"). The
    remote check matters too: after a `git tag -d vX.Y.Z` to retry a
    botched run, the tag may still be live on origin; without this
    probe, release_publish.sh would push the branch first and the
    tag push would then be rejected, leaving the remote in a
    half-released state. Catch both before touching any file.
    """
    tag = f"v{version}"
    local = subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", tag],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    if local.returncode == 0:
        existing = local.stdout.strip()
        fail(
            f"tag {tag} already exists locally at {existing[:7]} — delete it "
            f"first with `git tag -d {tag}` if you want to redo this release"
        )
    # ls-remote needs network; fail closed on transport error rather
    # than skip the check and risk a half-released remote state.
    remote = subprocess.run(
        ["git", "ls-remote", "--tags", "origin", f"refs/tags/{tag}"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    if remote.returncode != 0:
        fail(
            f"git ls-remote failed while checking origin for {tag} "
            f"(exit {remote.returncode}): {remote.stderr.strip()}\n"
            "       Refusing to proceed without confirming the remote tag is absent."
        )
    if remote.stdout.strip():
        fail(
            f"tag {tag} already exists on origin — delete it on the remote "
            f"(`git push origin :refs/tags/{tag}`) before retrying, or pick "
            "the next patch number"
        )


def verify_branch() -> str:
    branch = run(["git", "branch", "--show-current"])
    if not MAINTENANCE_BRANCH_RE.match(branch):
        fail(
            f"current branch '{branch}' is not an N.N.x maintenance branch — "
            "patches are only cut from maintenance branches"
        )
    return branch


def verify_version_matches_branch(branch: str, version: str) -> None:
    """Reject `release-patch 0.8.2` run from a `0.9.x` checkout.

    The manifest is a weak signal — a stale or wrongly-backported
    manifest on a maintenance branch would otherwise let us tag an
    out-of-range version on that branch. The branch name is the
    canonical line identifier; the version's major/minor must match.
    """
    branch_major, branch_minor, _ = branch.split(".")
    m = SEMVER_RE.match(version)
    if not m:
        fail(f"non-semver version: {version!r}")
    v_major, v_minor, _ = m.groups()
    if (v_major, v_minor) != (branch_major, branch_minor):
        fail(
            f"version {version} is out of range for branch {branch} — "
            f"expected {branch_major}.{branch_minor}.x"
        )


def read_manifest_version() -> str:
    return json.loads(MANIFEST_PATH.read_text())["version"]


def write_manifest_version(version: str) -> None:
    # Preserve key order + trailing newline. json.dump strips trailing
    # newline so re-add it; the original file ended with one.
    data = json.loads(MANIFEST_PATH.read_text())
    data["version"] = version
    MANIFEST_PATH.write_text(json.dumps(data, indent=2) + "\n")


def verify_patch_bump(current: str, requested: str) -> None:
    m_cur = SEMVER_RE.match(current)
    m_new = SEMVER_RE.match(requested)
    if not m_cur or not m_new:
        fail(f"non-semver version: current={current!r} requested={requested!r}")
    cur = tuple(int(x) for x in m_cur.groups())
    new = tuple(int(x) for x in m_new.groups())
    if new[:2] != cur[:2]:
        fail(
            f"requested {requested} is not a patch bump from {current} — "
            "major/minor must match (patch releases only)"
        )
    if new[2] != cur[2] + 1:
        # Skipping patch numbers (e.g. 0.9.1 → 0.9.20) is almost always
        # a typo. Refuse to leave gaps so the version history stays a
        # contiguous sequence per minor line, matching what
        # semantic-release would have produced.
        fail(
            f"requested {requested} skips patch numbers — next patch "
            f"after {current} is {cur[0]}.{cur[1]}.{cur[2] + 1}"
        )


def last_tag() -> str | None:
    """Last semver tag reachable from HEAD on this branch."""
    try:
        return run(["git", "describe", "--tags", "--abbrev=0", "--match", "v*"])
    except subprocess.CalledProcessError:
        return None


# Footer forms accepted by the Conventional Commits spec for major bumps.
# Mirrors what @semantic-release/commit-analyzer recognises so we stay in
# lockstep with the CI release tool on the same commits.
BREAKING_FOOTER_RE = re.compile(r"^BREAKING[ -]CHANGE:", re.MULTILINE)


def collect_commits(
    since_tag: str | None,
) -> tuple[dict[str, list[tuple[str, str, str]]], list[tuple[str, str]]]:
    """Walk commits since `since_tag`, grouping by conventional type.

    Returns:
        (groups, breaking)
        - `groups` maps section type → list of (sha, scope, subject).
          Merge commits are excluded here — they restate the work
          captured in their child commits.
        - `breaking` lists (sha, subject) for commits that require a
          major bump (`!` marker on type/scope OR a `BREAKING CHANGE:`
          / `BREAKING-CHANGE:` footer in the body). Merge commits are
          included in this scan — GitLab MR squashes occasionally land
          the breaking footer in the merge subject/body even when no
          child commit carries it, and missing that would let a major
          change ship under a patch version.
    """
    rev_range = f"{since_tag}..HEAD" if since_tag else "HEAD"
    # %P = parent SHAs (space-separated); >1 parent = merge commit.
    # %x1e between subject and body, %x1f between commits — both ASCII
    # control characters that can't appear in commit text.
    raw = run(
        [
            "git",
            "log",
            rev_range,
            "--pretty=format:%H%x09%P%x09%s%x1e%b%x1f",
        ]
    )
    groups: dict[str, list[tuple[str, str, str]]] = {t: [] for t, _ in SECTION_ORDER}
    breaking: list[tuple[str, str]] = []
    if not raw:
        return groups, breaking
    for chunk in raw.split("\x1f"):
        chunk = chunk.strip("\n")
        if not chunk:
            continue
        head, _, body = chunk.partition("\x1e")
        sha, parents, subject = head.split("\t", 2)
        subject = subject.strip()
        is_merge = len(parents.split()) > 1

        # Breaking-change detection runs on EVERY commit, merges
        # included. The subject of a merge commit usually isn't
        # conventional ("Merge branch 'foo' ..."), so the `!` marker
        # check below will only fire on conventional subjects; the
        # body-footer scan catches the merge-commit case.
        m = CONVENTIONAL_RE.match(subject)
        if m and m.group("breaking"):
            breaking.append((sha, subject))
        elif BREAKING_FOOTER_RE.search(body or ""):
            breaking.append((sha, subject))

        # CHANGELOG grouping only applies to non-merge commits.
        if is_merge:
            continue
        if m:
            ctype = m.group("type")
            scope = m.group("scope") or ""
            subj = m.group("subject")
            if ctype in groups:
                groups[ctype].append((sha, scope, subj))
            continue
        # Fall through: try the `git revert "..."` form.
        m_rev = REVERT_SUBJECT_RE.match(subject)
        if m_rev and "revert" in groups:
            # Keep the full "Revert \"...\"" subject so the changelog
            # entry matches what semantic-release would have written.
            groups["revert"].append((sha, "", subject))
    return groups, breaking


def verify_patch_level(
    groups: dict[str, list[tuple[str, str, str]]],
    breaking: list[tuple[str, str]],
) -> None:
    """Refuse to ship a minor/major change under a patch version.

    The patch flow is only for `fix:`, `perf:`, `revert:` and
    refactor-class commits. A `feat:` requires a minor bump and any
    breaking change requires a major bump — semantic-release on `main`
    enforces the same rules and the manual flow must not diverge.
    """
    if breaking:
        offenders = "\n".join(f"    {sha[:7]} {subj}" for sha, subj in breaking)
        fail(
            "refusing to ship a patch — the range contains breaking "
            f"changes that require a major bump:\n{offenders}"
        )
    feats = groups.get("feat", [])
    if feats:
        offenders = "\n".join(
            f"    {sha[:7]} {('('+scope+') ') if scope else ''}{subj}"
            for sha, scope, subj in feats
        )
        fail(
            "refusing to ship a patch — the range contains feat: commits "
            f"that require a minor bump:\n{offenders}"
        )


def render_changelog_section(
    version: str,
    prev_tag: str | None,
    groups: dict[str, list[tuple[str, str, str]]],
) -> str:
    today = date.today().isoformat()
    if prev_tag:
        header_link = f"[{version}]({COMPARE_URL}/compare/{prev_tag}...v{version})"
    else:
        header_link = version
    lines = [f"## {header_link} ({today})", ""]
    has_patch_producing = False
    for ctype, title in SECTION_ORDER:
        entries = groups.get(ctype, [])
        if not entries:
            continue
        if ctype in PATCH_PRODUCING_TYPES:
            has_patch_producing = True
        lines.append(f"### {title}")
        lines.append("")
        # Sort by scope then subject for stable ordering, matching
        # release-notes-generator's default conventional preset.
        for sha, scope, subj in sorted(entries, key=lambda e: (e[1], e[2])):
            short = sha[:7]
            url = f"{COMPARE_URL}/commit/{sha}"
            prefix = f"**{scope}:** " if scope else ""
            lines.append(f"* {prefix}{subj} ([{short}]({url}))")
        lines.append("")
    if not has_patch_producing:
        fail(
            "no fix/perf/revert commits since the last tag — nothing to "
            "release. (refactor/chore/docs/style/test/ci/build don't "
            "trigger a release per release.config.js.)"
        )
    return "\n".join(lines)


def prepend_changelog(section: str) -> None:
    existing = CHANGELOG_PATH.read_text()
    CHANGELOG_PATH.write_text(section + "\n" + existing)


def build_frontend() -> None:
    print("→ rebuilding frontend bundle...", flush=True)
    subprocess.run(["npm", "run", "build"], cwd=FRONTEND_DIR, check=True)


# Files mutated by the prep phase OR by the frontend build.
#
# The build script runs prettier over src/**/*.js + build.js + postbuild.js
# before invoking esbuild, then over the bundled panel.js after. Any of
# those files can be modified by the build even if they weren't touched
# by us directly — so the commit must include them, and a rollback must
# restore them.
#
# The release-commit stage and the rollback path both operate on this
# set, with the two release-only files (CHANGELOG.md + manifest.json)
# layered on top.
PREP_MUTATED_PATHS = (
    "CHANGELOG.md",
    "custom_components/selora_ai/manifest.json",
)
BUILD_TOUCHED_PATHS = (
    # Whole frontend dir — git respects .gitignore so node_modules etc.
    # don't get staged. Both prettier (src + build.js + postbuild.js)
    # and esbuild (panel.js) write inside this tree.
    "custom_components/selora_ai/frontend",
)
ALL_RELEASE_PATHS = PREP_MUTATED_PATHS + BUILD_TOUCHED_PATHS


def restore_prep_files() -> None:
    """Discard prep-phase + build edits via `git checkout --`.

    All paths are tracked at HEAD, so a checkout cleanly reverts to
    the pre-prep state. Errors here are swallowed — we're already on
    a failure path and would rather surface the original cause.
    """
    subprocess.run(
        ["git", "checkout", "--", *ALL_RELEASE_PATHS],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
    )


def git_commit_and_tag(version: str) -> None:
    tag = f"v{version}"
    # Stage the prep mutations + the whole frontend tree. `git add
    # <dir>` follows .gitignore so node_modules / dist artifacts don't
    # sneak in. Since verify_clean_tree() ran at the top of main(),
    # anything dirty inside these paths was introduced by us
    # (manifest/CHANGELOG bumps) or by `npm run build` (prettier
    # rewrites + esbuild output) — both legitimate parts of the release.
    run(["git", "add", *ALL_RELEASE_PATHS])
    # Defensive: confirm staging captured everything. A leftover dirty
    # path here means the build wrote outside BUILD_TOUCHED_PATHS, in
    # which case release-publish's clean-tree preflight would later
    # reject the retry. Better to fail now with the exact path list.
    leftover = run(["git", "status", "--porcelain"])
    unstaged = [line for line in leftover.splitlines() if line[:2].strip() and line[1] != " "]
    if unstaged:
        fail(
            "unexpected unstaged changes after build — extend "
            "BUILD_TOUCHED_PATHS to cover these or fix the build "
            "to keep them stable:\n" + "\n".join(unstaged)
        )
    subprocess.run(
        ["git", "commit", "-m", f"chore(release): {version} [skip ci]"],
        cwd=REPO_ROOT,
        check=True,
    )
    subprocess.run(["git", "tag", tag], cwd=REPO_ROOT, check=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("version", help="patch version to release, e.g. 0.9.2")
    args = parser.parse_args()

    verify_clean_tree()
    branch = verify_branch()
    verify_version_matches_branch(branch, args.version)
    verify_tag_absent(args.version)
    current = read_manifest_version()
    verify_patch_bump(current, args.version)

    prev = last_tag()
    groups, breaking = collect_commits(prev)
    verify_patch_level(groups, breaking)
    section = render_changelog_section(args.version, prev, groups)

    print(f"→ branch:        {branch}")
    print(f"→ current ver:   {current}")
    print(f"→ target ver:    {args.version}")
    print(f"→ previous tag:  {prev or '(none)'}")
    print()
    print("---- CHANGELOG entry ----")
    print(section)
    print("-------------------------")
    print()
    write_manifest_version(args.version)
    prepend_changelog(section)
    try:
        build_frontend()
    except subprocess.CalledProcessError as exc:
        # Roll back the manifest + CHANGELOG mutations so the next
        # invocation isn't blocked by a dirty tree. The build itself
        # may have updated panel.js partially; restore that too.
        restore_prep_files()
        fail(
            "frontend build failed — release files rolled back. "
            f"Fix the build error and retry. (npm exit code: {exc.returncode})"
        )
    git_commit_and_tag(args.version)
    print()
    print(f"✓ committed + tagged v{args.version} locally")
    print(f"→ next: review the diff, then run `just release-publish {args.version}`")


if __name__ == "__main__":
    main()
