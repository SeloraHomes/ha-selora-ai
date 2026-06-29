// semantic-release configuration
// Docs: https://semantic-release.gitbook.io/semantic-release/
//
// Two modes, selected by the PRERELEASE env var:
//
//   Stable (PRERELEASE unset) — runs on a scheduled/manual ("web") pipeline on
//   main, or a manual pipeline on a `N.N.x` maintenance branch. Cuts a clean
//   `vX.Y.Z`, commits the CHANGELOG + manifest bump back, and publishes to both
//   mirrors. This is the only release HACS surfaces to normal users.
//
//   Prerelease (PRERELEASE=true) — runs on every push to the `next` branch.
//   main stays the stable release branch (semantic-release requires at least
//   one), and `next` is the prerelease channel: it cuts a `vX.Y.Z-pre.N` tag
//   and publishes it, but does NOT commit anything back (no CHANGELOG/manifest
//   churn) and marks the GitHub release as a prerelease so HACS ignores it
//   unless a user opts into betas. CI keeps `next` fast-forwarded to `main`
//   (the `mirror-next` job), so every push to main produces a fresh prerelease
//   — `next` is a machine-managed mirror, never committed to directly.
//
// Requires CI variables:
//   GL_TOKEN  — GitLab project access token (write_repository + api scope)
//   GH_TOKEN  — GitHub personal access token (repo scope) for the HACS mirror
//
// Commit types that produce a release:
//   feat:            → minor bump  (0.1.0 → 0.2.0)
//   fix: / perf: / refactor: → patch bump  (0.1.0 → 0.1.1)
//   BREAKING CHANGE  → major bump  (0.1.0 → 1.0.0)
//   docs: / chore: / style: / test: → no release

const isPrerelease = process.env.PRERELEASE === "true";

const plugins = [
  // 1. Analyse commits to determine the next version
  [
    "@semantic-release/commit-analyzer",
    {
      preset: "conventionalcommits",
      releaseRules: [
        { type: "feat",     release: "minor" },
        { type: "fix",      release: "patch" },
        { type: "perf",     release: "patch" },
        { type: "refactor", release: false   },
        { type: "revert",   release: "patch" },
        { type: "docs",     release: false   },
        { type: "chore",    release: false   },
        { type: "style",    release: false   },
        { type: "test",     release: false   },
        { breaking: true,   release: "major" },
      ],
    },
  ],

  // 2. Generate human-readable release notes
  [
    "@semantic-release/release-notes-generator",
    {
      preset: "conventionalcommits",
      presetConfig: {
        types: [
          { type: "feat",     section: "Features"         },
          { type: "fix",      section: "Bug Fixes"        },
          { type: "perf",     section: "Performance"      },
          { type: "refactor", section: "Code Refactoring" },
          { type: "revert",   section: "Reverts"          },
          { type: "docs",     section: "Documentation", hidden: true },
          { type: "chore",    section: "Chores",        hidden: true },
        ],
      },
    },
  ],

  // 3. Prepend release notes to CHANGELOG.md
  //    Even in prerelease mode this writes the file on disk so the
  //    github-release script can extract the notes — it just isn't committed.
  [
    "@semantic-release/changelog",
    { changelogFile: "CHANGELOG.md" },
  ],

  // 4. Bump manifest.json then rebuild the frontend so the bundle picks up the new version
  [
    "@semantic-release/exec",
    {
      prepareCmd:
        "sed -i 's/\"version\": \".*\"/\"version\": \"${nextRelease.version}\"/' custom_components/selora_ai/manifest.json" +
        " && cd custom_components/selora_ai/frontend && npm run build && cd ../../..",
    },
  ],

  // 5. (stable only) Commit the updated CHANGELOG.md + manifest.json back to main.
  //    Skipped for prereleases so a plain push to main doesn't churn the branch
  //    with a [skip ci] commit on every merge.
  ...(isPrerelease
    ? []
    : [
        [
          "@semantic-release/git",
          {
            assets: [
              "CHANGELOG.md",
              "custom_components/selora_ai/manifest.json",
            ],
            message:
              "chore(release): ${nextRelease.version} [skip ci]\n\n${nextRelease.notes}",
          },
        ],
      ]),

  // 6. Create the GitLab tag + release page
  [
    "@semantic-release/gitlab",
    {
      gitlabUrl: "https://gitlab.com",
      gitlabApiPathPrefix: "/api/v4",
    },
  ],

  // 7. Create matching GitHub release (HACS reads tags/releases from the mirror)
  //    The @semantic-release/github plugin reads the repo from the git origin
  //    (GitLab nested path) and cannot be overridden per-plugin, so we use exec.
  //    github-release.mjs marks any version with a prerelease identifier
  //    (e.g. -pre.1) as a GitHub prerelease, so HACS keeps it off by default.
  [
    "@semantic-release/exec",
    {
      publishCmd: "node scripts/github-release.mjs ${nextRelease.version}",
    },
  ],
];

export default {
  // Prerelease: a dedicated `next` channel producing `vX.Y.Z-pre.N`.
  // Stable: main plus maintenance branches (e.g. `0.8.x`) cut from a release
  // tag, which let us ship patch releases without merging unrelated main-branch
  // work. The glob pattern matches `N.x`, `N.N.x`, etc. — semantic-release
  // enforces that the version stays inside the branch's range.
  branches: isPrerelease
    ? ["main", { name: "next", prerelease: "pre" }]
    : ["+([0-9])?(.{+([0-9]),x}).x", "main"],
  tagFormat: "v${version}",

  plugins,
};
