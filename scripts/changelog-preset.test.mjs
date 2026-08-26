// The release job runs `npx semantic-release`, and nothing else does — it is
// gated to a scheduled or manual pipeline on the default branch. So every step
// semantic-release performs is unexercised until the moment a release is being
// cut, which is the worst time to discover that one of them cannot run.
//
// `generateNotes` is the step that has actually broken. The conventionalcommits
// preset ships a handlebars template, `conventional-changelog-writer` renders
// it, and the two are versioned independently: preset 10.4.0 requires writer 9,
// while the newest stable @semantic-release/release-notes-generator (14.1.1)
// still depends on writer ^8. A Renovate bump to the preset is therefore green
// in its own merge request — no job renders anything — and the failure surfaces
// days later as a release that analyses 34 commits, decides on 0.16.0, and dies
// with `Missing helper` before tagging anything.
//
// This renders the preset through whichever writer the lockfile actually
// resolves. It is the same pairing the release job gets, so an incompatible
// combination fails HERE, in the merge request that introduced it.
import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { writeChangelogString } from "conventional-changelog-writer";
import preset from "conventional-changelog-conventionalcommits";

const commit = (type, subject, hash) => ({
  type,
  scope: null,
  subject,
  header: `${type}: ${subject}`,
  hash,
  body: null,
  footer: null,
  notes: [],
  references: [],
  mentions: [],
  revert: null,
});

describe("the changelog preset and the resolved writer", () => {
  it("renders release notes without a missing helper", async () => {
    const config = await preset({});
    // The preset changed shape between majors: 9 returns `writerOpts`, 10
    // returns `writer`. Reading only one of them is how this test first came
    // to pass against the very combination it exists to reject — `writerOpts`
    // is undefined on 10, so the writer fell back to its own defaults and the
    // preset's planted guard was never rendered.
    const writerOpts = config.writer ?? config.writerOpts;
    assert.ok(writerOpts, "the preset exposed neither `writer` nor `writerOpts`");
    const notes = await writeChangelogString(
      [
        commit("feat", "a new capability", "0123456789abcdef"),
        commit("fix", "a corrected behaviour", "abcdef0123456789"),
      ],
      { version: "1.2.3" },
      writerOpts,
    );

    // A missing helper throws rather than returning bad output, so reaching
    // here is most of the assertion. The rest guards against a preset that
    // renders successfully but silently drops the commits — which would leave
    // a release whose notes are empty.
    assert.match(notes, /1\.2\.3/);
    assert.match(notes, /a new capability/);
    assert.match(notes, /a corrected behaviour/);
  });
});
