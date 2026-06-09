# Release process

Two flows. Pick by what you're shipping.

| Situation                                                       | Use                                             |
| --------------------------------------------------------------- | ----------------------------------------------- |
| Stable release from `main` (new minor, major, or first stable)  | CI / semantic-release                           |
| Patch on an `N.N.x` maintenance branch                          | Manual: `just release-patch` + `release-publish`|

Both flows produce the same artifacts:

- GitLab tag `vX.Y.Z` + release page
- GitHub release on `SeloraHomes/ha-selora-ai` (HACS reads from here)
- `selora_ai.zip` asset on the GitHub release (HACS installs this)
- Bumped `custom_components/selora_ai/manifest.json`
- Prepended `CHANGELOG.md` section

## Stable release from `main` (semantic-release)

Config: `release.config.js`. Plugins run in order:

1. **commit-analyzer** — picks bump (`feat:` → minor, `fix:`/`perf:`/`revert:` → patch, `BREAKING CHANGE` → major).
2. **release-notes-generator** — renders the CHANGELOG section.
3. **changelog** — prepends to `CHANGELOG.md`.
4. **exec (prepareCmd)** — bumps `manifest.json`, rebuilds the frontend bundle.
5. **git** — commits `chore(release): X.Y.Z [skip ci]` back to `main`.
6. **gitlab** — creates the GitLab tag + release page.
7. **exec (publishCmd)** — runs `scripts/github-release.mjs` to mirror to GitHub and upload the HACS zip.

### Trigger

The `release` job in `.gitlab-ci.yml` is manual-only:

- Web-pipeline trigger on `main` → cuts the next stable.
- Web-pipeline trigger on an `N.N.x` branch → semantic-release attempts a patch (but see the deadlock below).
- Schedule trigger on `main` → also cuts the next stable.

GitLab → Build → Pipelines → Run pipeline → select branch.

### Required CI variables

| Variable   | Scope                           | What it's for                                  |
| ---------- | ------------------------------- | ---------------------------------------------- |
| `GL_TOKEN` | Project access token            | Pushes the `[skip ci]` commit + creates tags   |
| `GH_TOKEN` | GitHub PAT, `repo` scope        | Creates the GitHub release on the HACS mirror  |

Both must be masked + protected in Settings → CI/CD → Variables. The
project's `protected_tags` ACL is set so only the semantic-release CI
user (id `35769985`) can push `v*` tags.

## Patch on a maintenance branch (manual)

semantic-release **cannot** cut a patch off an `N.N.x` branch when
`main` is still at the same minor and holds queued `feat:` commits.
The maintenance branch's range collapses to empty because the
ceiling = lowest stable tag on a higher channel = `main`'s latest tag.
See [the maintenance deadlock](#maintenance-deadlock-why-the-manual-flow-exists)
below.

The manual flow uses two `just` recipes that together reproduce what
semantic-release would have done:

```bash
git checkout 0.9.x                  # ON the maintenance branch
# merge / cherry-pick your fix MRs first

just release-patch 0.9.2            # local prep — see below
git diff HEAD~1                     # review what's about to ship
just release-publish 0.9.2          # push + GitHub + GitLab
```

### `just release-patch X.Y.Z`

Runs `scripts/release_patch.py`. Guards:

- Working tree must be clean.
- `vX.Y.Z` must not already exist locally or on `origin`. Local
  collision: `git tag -d vX.Y.Z`. Remote collision (typically
  after a botched earlier publish where the tag pushed but the
  release page didn't finish): `git push origin :refs/tags/vX.Y.Z`.
  Without the remote check, prep would happily build a divergent
  release commit and `release-publish` would push the branch
  before the tag push got rejected, leaving the remote in a
  half-released state.

If `npm run build` fails mid-prep, the manifest, CHANGELOG, and
the entire frontend tree are rolled back via `git checkout --`
before the script exits so a retry isn't blocked by a dirty tree.

The release commit stages every file under
`custom_components/selora_ai/frontend/` (alongside the manifest +
CHANGELOG). This is necessary because `node build.js` runs
prettier over `src/**/*.js`, `build.js`, and `postbuild.js` before
bundling, so unformatted sources get rewritten as part of the
build. If the build leaves any path dirty outside this tree, the
script fails loud with the offending list rather than committing
a release that the retry guards can't recover.
- Branch name must match `N.N.x`.
- `X.Y.Z`'s major + minor must match the branch (e.g. only `0.9.*` from
  `0.9.x`). Catches the case where a backported or stale `manifest.json`
  would otherwise let the bump slip past.
- `X.Y.Z` must be a strict consecutive patch bump from `manifest.json`'s
  current version: major + minor match, patch is exactly +1. Skipping
  patch numbers (`0.9.1 → 0.9.20`) is rejected — almost always a typo.
- The commit range since the last tag must contain at least one
  `fix:`, `perf:`, or `revert:` (the types `release.config.js` maps
  to `release: patch`). Standard `git revert` commits — whose
  subject is `Revert "..."` rather than `revert: ...` — are also
  recognised as patch-producing reverts, matching what
  semantic-release does. A range of only
  `refactor:`/`chore:`/`docs:` produces no release on `main` via
  semantic-release and is rejected here too.
- The range must contain only patch-level changes. The script refuses
  to ship if it sees a `feat:` (minor bump) or any `!` marker /
  `BREAKING CHANGE:` footer (major bump) — those need a different
  flow so the version doesn't lie about what's inside. Merge commits
  are scanned for the breaking footer too (GitLab squash merges
  sometimes carry it on the merge itself).

Then:

1. Walks `git log <last-tag>..HEAD --no-merges` and groups conventional
   commits into Features / Bug Fixes / Performance / Code Refactoring /
   Reverts. Order matches the semantic-release preset.
2. Aborts if no releasable commits are found (don't ship an empty patch).
3. Renders the section, bumps `manifest.json`, runs `npm run build` in
   the frontend dir, prepends `CHANGELOG.md`.
4. Commits `chore(release): X.Y.Z [skip ci]` and tags `vX.Y.Z` locally.

Nothing is pushed yet.

### `just release-publish X.Y.Z`

Runs `scripts/release_publish.sh`. Requires `GH_TOKEN` (falls back to
`gh auth token` if the env var is unset and the `gh` CLI is logged in).

Pre-flight guards (fail before anything is pushed):

- `HEAD` must equal the tag's commit. The HACS zip is built from the
  current checkout, so if HEAD has advanced past the tag the zip would
  contain code that isn't in `vX.Y.Z`.
- Working tree must be clean. Uncommitted edits would otherwise leak
  into the zip and ship under the wrong version number.

Then:

1. Pushes the maintenance branch.
2. Pushes the tag.
3. Verifies the maintenance commit exists on the GitHub mirror. The
   GitLab → GitHub mirror sync is async and can lag by a minute or
   two; without this check, anchoring a release on a SHA the mirror
   doesn't have yet returns a misleading 422 from `POST /releases`
   that downstream code mistakes for "release already exists". Also
   verifies that, if the `vX.Y.Z` tag already exists on the mirror,
   it points at the same commit — otherwise the release page would
   keep serving the old tag's source archives while we upload the
   right zip on top, a silent mismatch HACS source-archive users
   would hit.
4. Runs `scripts/github-release.mjs vX.Y.Z <sha>` — builds
   `selora_ai.zip`, creates the GitHub release **anchored at the
   maintenance commit** (not `main`, otherwise the auto-generated
   source archives ship main's code under a maintenance tag), uploads
   the asset.
5. Creates the GitLab release page via `glab api`. Idempotent — if a
   release already exists for the tag (rerun after a partial failure)
   it's updated in place via `PUT` instead of POSTing a duplicate.
6. Cleans up the local `selora_ai.zip`.

### Tag protection ACL

`v*` tags are protected. The CI user (`35769985`) is the only allowed
creator. If the tag push fails, the script prints the
unprotect/reprotect ACL and stops — flipping project security is your
call, not the script's. To unblock:

```bash
PROJECT_ID="selorahomes%2Fproducts%2Fselora-ai%2Fha-integration"

# 1. Unprotect
glab api --method DELETE "projects/${PROJECT_ID}/protected_tags/v*"

# 2. Re-run
just release-publish 0.9.2

# 3. Re-protect with the original ACL
glab api --method POST "projects/${PROJECT_ID}/protected_tags" \
  -f 'name=v*' \
  -f 'allowed_to_create[][access_level]=0' \
  -f 'allowed_to_create[][user_id]=35769985'
```

## Maintenance deadlock (why the manual flow exists)

semantic-release computes a maintenance branch's publishable range as:

```
>= last_release_on_branch  <  highest_release_on_higher_channels
```

For `0.9.x` after `v0.9.0` shipped from `main`:

- Lower bound: `>= 0.9.0` (last tag reachable on the branch).
- Upper bound: `< 0.9.0` (lowest tag on a higher channel — `main` still
  sits at `v0.9.0`).

Range collapses to empty. semantic-release fails with `EINVALIDNEXTVERSION`.

The fix that semantic-release expects: cut a new minor on `main` first
(`v0.10.0`) so the maintenance ceiling moves up. That's incompatible
with shipping a patch *while* `main` has unreleased `feat:` commits
that aren't ready for stable — exactly the situation we hit on `v0.9.1`.

Explicit `range: "0.9.x"` on the maintenance branch does **not** bypass
this — it's intersected with the higher-channel ceiling. Prerelease
branches (`next`) might help if they hold a tag higher than `main`,
but only by *also* shipping something on `next` first (an empty `next`
doesn't move the ceiling). At that point the manual flow is simpler.

## Future: graduating to a real release-train

If patch-while-main-is-stale becomes routine, two real options:

1. **release-please** (Google) or **changesets** — multi-track release
   tools that don't enforce the ceiling rule.
2. **`next` prerelease branch** — feats land on `next`, semantic-release
   cuts `v0.10.0-pre.1` etc., `main` only moves when promoting. Active
   dev moves off `main`. Reorg cost is real, document migration in
   `docs/adr/` before committing.

For now: keep semantic-release on `main` for stable cuts; use the
manual recipes for patches. Both produce identical artifacts.
