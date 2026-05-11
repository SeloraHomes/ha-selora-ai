// semantic-release configuration
// Docs: https://semantic-release.gitbook.io/semantic-release/
//
// Trigger: runs automatically in GitLab CI on every push to main.
// Requires CI variables:
//   GL_TOKEN  — GitLab project access token (write_repository + api scope)
//   GH_TOKEN  — GitHub personal access token (repo scope) for the HACS mirror
//
// Commit types that produce a release:
//   feat:            → minor bump  (0.1.0 → 0.2.0)
//   fix: / perf: / refactor: → patch bump  (0.1.0 → 0.1.1)
//   BREAKING CHANGE  → major bump  (0.1.0 → 1.0.0)
//   docs: / chore: / style: / test: → no release

export default {
  // Maintenance branches (e.g. `0.8.x`) cut from a release tag let us ship
  // patch releases without merging unrelated main-branch work. The glob
  // pattern matches `N.x`, `N.N.x`, etc. — semantic-release enforces that
  // the version stays inside the branch's range.
  branches: ["+([0-9])?(.{+([0-9]),x}).x", "main"],
  tagFormat: "v${version}",

  plugins: [
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

    // 5. Commit the updated CHANGELOG.md + manifest.json back to main
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
    [
      "@semantic-release/exec",
      {
        publishCmd: "node scripts/github-release.mjs ${nextRelease.version}",
      },
    ],
  ],
};
