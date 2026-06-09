"""Unit tests for the pure functions in release_patch.py.

The IO-heavy bits (`build_frontend`, `git_commit_and_tag`,
`verify_clean_tree`, `verify_branch`) are skipped — they shell out to
git/npm and there's no useful boundary to mock at this scale. We cover
the parsing + rendering logic that's easy to get wrong silently.
"""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

# Importing as a module under the `scripts/` package would require an
# `__init__.py`; load it by path instead to keep scripts/ unchanged.
_SPEC = importlib.util.spec_from_file_location(
    "release_patch", Path(__file__).parent / "release_patch.py"
)
release_patch = importlib.util.module_from_spec(_SPEC)
sys.modules["release_patch"] = release_patch
_SPEC.loader.exec_module(release_patch)


class VerifyPatchBumpTests(unittest.TestCase):
    def test_accepts_next_consecutive_patch(self) -> None:
        release_patch.verify_patch_bump("0.9.0", "0.9.1")
        release_patch.verify_patch_bump("0.9.5", "0.9.6")

    def test_rejects_skipped_patch(self) -> None:
        # 0.9.1 → 0.9.20 is almost always a typo for 0.9.2.
        with self.assertRaises(SystemExit):
            release_patch.verify_patch_bump("0.9.1", "0.9.20")
        with self.assertRaises(SystemExit):
            release_patch.verify_patch_bump("0.9.0", "0.9.2")

    def test_rejects_minor_bump(self) -> None:
        with self.assertRaises(SystemExit):
            release_patch.verify_patch_bump("0.9.0", "0.10.0")

    def test_rejects_major_bump(self) -> None:
        with self.assertRaises(SystemExit):
            release_patch.verify_patch_bump("0.9.0", "1.0.0")

    def test_rejects_same_version(self) -> None:
        with self.assertRaises(SystemExit):
            release_patch.verify_patch_bump("0.9.0", "0.9.0")

    def test_rejects_downgrade(self) -> None:
        with self.assertRaises(SystemExit):
            release_patch.verify_patch_bump("0.9.5", "0.9.4")

    def test_rejects_non_semver(self) -> None:
        with self.assertRaises(SystemExit):
            release_patch.verify_patch_bump("0.9.0", "0.9.1-pre")


class VerifyVersionMatchesBranchTests(unittest.TestCase):
    def test_accepts_matching_branch(self) -> None:
        release_patch.verify_version_matches_branch("0.9.x", "0.9.1")
        release_patch.verify_version_matches_branch("0.9.x", "0.9.42")
        release_patch.verify_version_matches_branch("1.10.x", "1.10.3")

    def test_rejects_wrong_minor(self) -> None:
        with self.assertRaises(SystemExit):
            release_patch.verify_version_matches_branch("0.9.x", "0.8.2")
        with self.assertRaises(SystemExit):
            release_patch.verify_version_matches_branch("0.9.x", "0.10.0")

    def test_rejects_wrong_major(self) -> None:
        with self.assertRaises(SystemExit):
            release_patch.verify_version_matches_branch("0.9.x", "1.9.0")

    def test_rejects_non_semver(self) -> None:
        with self.assertRaises(SystemExit):
            release_patch.verify_version_matches_branch("0.9.x", "0.9.1-rc")


class RenderChangelogSectionTests(unittest.TestCase):
    def _groups(self, **overrides):
        base = {ctype: [] for ctype, _ in release_patch.SECTION_ORDER}
        base.update(overrides)
        return base

    def test_groups_by_section_and_sorts_by_scope(self) -> None:
        groups = self._groups(
            fix=[
                ("abcdef1234567", "ui", "wrap chip on long names"),
                ("1234567890abc", "chat", "guard stream events"),
            ],
            perf=[("deadbeefcafe1", "", "cut chat latency")],
        )
        section = release_patch.render_changelog_section("0.9.1", "v0.9.0", groups)
        self.assertIn("## [0.9.1]", section)
        self.assertIn("compare/v0.9.0...v0.9.1", section)
        self.assertIn("### Bug Fixes", section)
        self.assertIn("### Performance", section)
        # chat sorts before ui alphabetically.
        chat_pos = section.index("**chat:** guard stream events")
        ui_pos = section.index("**ui:** wrap chip on long names")
        self.assertLess(chat_pos, ui_pos)
        # Scopeless commits render without the "**scope:**" prefix.
        self.assertIn("* cut chat latency", section)

    def test_skips_empty_sections(self) -> None:
        groups = self._groups(fix=[("abcdef1234567", "", "single fix")])
        section = release_patch.render_changelog_section("0.9.1", "v0.9.0", groups)
        self.assertIn("### Bug Fixes", section)
        self.assertNotIn("### Performance", section)
        self.assertNotIn("### Features", section)

    def test_aborts_on_empty_groups(self) -> None:
        with self.assertRaises(SystemExit):
            release_patch.render_changelog_section("0.9.1", "v0.9.0", self._groups())

    def test_aborts_on_refactor_only(self) -> None:
        # refactor is `release: false` in release.config.js, so a
        # refactor-only range must not produce a patch even though
        # the section renders.
        groups = self._groups(
            refactor=[("aaa1111", "llm", "split monolith")],
        )
        with self.assertRaises(SystemExit):
            release_patch.render_changelog_section("0.9.1", "v0.9.0", groups)

    def test_renders_refactor_alongside_patch_producing(self) -> None:
        # When at least one fix/perf/revert is present, refactor entries
        # are kept in the changelog (they describe what shipped) but
        # are not what *triggered* the release.
        groups = self._groups(
            fix=[("aaa1111", "", "real fix")],
            refactor=[("bbb2222", "", "tidy")],
        )
        section = release_patch.render_changelog_section("0.9.1", "v0.9.0", groups)
        self.assertIn("### Bug Fixes", section)
        self.assertIn("### Code Refactoring", section)

    def test_omits_compare_link_when_no_previous_tag(self) -> None:
        groups = self._groups(fix=[("abcdef1234567", "", "first fix")])
        section = release_patch.render_changelog_section("0.1.0", None, groups)
        self.assertIn("## 0.1.0", section)
        self.assertNotIn("compare/", section)


class VerifyPatchLevelTests(unittest.TestCase):
    def _groups(self, **overrides):
        base = {ctype: [] for ctype, _ in release_patch.SECTION_ORDER}
        base.update(overrides)
        return base

    def test_passes_with_only_fix_perf_revert(self) -> None:
        groups = self._groups(
            fix=[("aaa1111", "", "first fix")],
            perf=[("bbb2222", "chat", "speed up render")],
            revert=[("ccc3333", "", "revert flaky test")],
        )
        release_patch.verify_patch_level(groups, [])

    def test_rejects_feat_under_patch(self) -> None:
        groups = self._groups(feat=[("ddd4444", "ui", "new tab")])
        with self.assertRaises(SystemExit):
            release_patch.verify_patch_level(groups, [])

    def test_rejects_breaking_change(self) -> None:
        groups = self._groups(fix=[("eee5555", "", "minor fix")])
        with self.assertRaises(SystemExit):
            release_patch.verify_patch_level(
                groups, [("eee5555", "fix(api)!: drop /v1")]
            )

    def test_breaking_takes_priority_over_feat(self) -> None:
        # When both are present, the breaking message must surface so
        # the operator can't bypass it by interpreting the failure as
        # "just drop the feat".
        groups = self._groups(feat=[("fff6666", "", "feat A")])
        with self.assertRaises(SystemExit) as ctx:
            release_patch.verify_patch_level(
                groups, [("ggg7777", "fix!: breaking subject")]
            )
        self.assertIsNotNone(ctx.exception)


class CollectCommitsBreakingDetectionTests(unittest.TestCase):
    """Spot-check that the breaking-change detector fires on both
    the `!` subject marker and the `BREAKING CHANGE:` body footer.

    Exercises the regex directly — `collect_commits` shells out to
    git which would need a real repo, not worth setting up here.
    """

    def test_subject_bang_marker(self) -> None:
        m = release_patch.CONVENTIONAL_RE.match("feat(api)!: drop /v1")
        self.assertEqual(m.group("breaking"), "!")

    def test_footer_breaking_change(self) -> None:
        body = "Some body text.\n\nBREAKING CHANGE: dropped /v1 endpoint."
        self.assertIsNotNone(release_patch.BREAKING_FOOTER_RE.search(body))

    def test_footer_breaking_change_hyphen(self) -> None:
        body = "BREAKING-CHANGE: dropped /v1 endpoint."
        self.assertIsNotNone(release_patch.BREAKING_FOOTER_RE.search(body))

    def test_no_breaking_marker(self) -> None:
        m = release_patch.CONVENTIONAL_RE.match("fix(api): minor adjustment")
        self.assertIsNone(m.group("breaking"))
        body = "Plain body. Nothing breaking here."
        self.assertIsNone(release_patch.BREAKING_FOOTER_RE.search(body))


class RevertSubjectTests(unittest.TestCase):
    """git revert produces 'Revert \"<original subject>\"' which isn't
    a conventional header. semantic-release recognises it as a
    patch-producing revert; this guard ensures the manual flow does
    too."""

    def test_matches_quoted_revert(self) -> None:
        m = release_patch.REVERT_SUBJECT_RE.match(
            'Revert "fix(chat): backend stream watchdog"'
        )
        self.assertIsNotNone(m)
        self.assertEqual(
            m.group("inner"), "fix(chat): backend stream watchdog"
        )

    def test_ignores_conventional_revert(self) -> None:
        # The conventional form `revert: ...` matches CONVENTIONAL_RE
        # already; this fallback must not double-count it.
        self.assertIsNone(
            release_patch.REVERT_SUBJECT_RE.match("revert: bring back X")
        )

    def test_ignores_non_revert(self) -> None:
        self.assertIsNone(
            release_patch.REVERT_SUBJECT_RE.match("fix(chat): foo")
        )
        self.assertIsNone(
            release_patch.REVERT_SUBJECT_RE.match("Reverted earlier change")
        )


class ConventionalParseTests(unittest.TestCase):
    """Spot-check CONVENTIONAL_RE on the commit shapes we actually ship."""

    def _match(self, subject: str):
        return release_patch.CONVENTIONAL_RE.match(subject)

    def test_parses_scoped_fix(self) -> None:
        m = self._match("fix(chat): backend stream watchdog + max-bytes cap")
        self.assertIsNotNone(m)
        self.assertEqual(m.group("type"), "fix")
        self.assertEqual(m.group("scope"), "chat")
        self.assertEqual(
            m.group("subject"), "backend stream watchdog + max-bytes cap"
        )

    def test_parses_scopeless_fix(self) -> None:
        m = self._match("fix: tighten input validation")
        self.assertEqual(m.group("type"), "fix")
        self.assertIsNone(m.group("scope"))

    def test_parses_breaking_marker(self) -> None:
        m = self._match("feat(api)!: drop legacy /v1 endpoints")
        self.assertEqual(m.group("type"), "feat")
        self.assertEqual(m.group("breaking"), "!")

    def test_ignores_non_conventional(self) -> None:
        self.assertIsNone(self._match("WIP: random thoughts"))
        self.assertIsNone(self._match("Merge branch 'foo'"))


if __name__ == "__main__":
    unittest.main()
