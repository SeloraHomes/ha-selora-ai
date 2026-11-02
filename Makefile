# Regeneration entry point for automated tooling.
#
# Day-to-day work uses the justfile — `just build` is the same build and stays
# the recipe to reach for by hand. This file exists because Renovate's
# postUpgradeTasks can only run commands its self-hosted config allows
# (`allowedCommands`/`allowedPostUpgradeCommands`, an admin-only setting in
# selorahomes/infra/renovate-runner, itself generated from terraform). `make
# generate` is already on that list as the group-wide convention for "refresh
# the committed artifacts a dependency bump invalidated" — connect uses it for
# templ/sqlc codegen. Reusing it keeps a dependency bump in this repo from
# needing an infra change.
#
# Why a dependency bump invalidates anything here: `npm run build` runs
# `prettier --write` over src/**/*.js, build.js and postbuild.js before it
# bundles, so a prettier bump reformats sources, and the `lint:prettier` job
# fails the MR unless the bump carries that reformat. The bundle itself is not
# committed.
#
# npm is not on PATH by default. Renovate provisions a toolchain only for the
# managers whose own artifact update needs one, so an npm or
# lock-file-maintenance branch has node because it just rewrote
# package-lock.json, while a github-actions, pip or pre-commit branch runs this
# target on a bare image and it dies as `npm: not found` — surfaced on the merge
# request as an artifact update failure rather than a red pipeline.
# `postUpgradeTasks.installTools` in renovate.json asks Containerbase for node
# on every branch instead. It is a no-op under `binarySource=global`; the
# runner image uses the default `install`.
#
# The task is deliberately NOT scoped to the frontend npm package instead, even
# though only that manager can change the formatting. `postUpgradeTasks` is
# allowed inside a `packageRules` entry, but in `executionMode: branch`
# Renovate reads it off the branch config, which is the FIRST upgrade's config
# after sorting by depName (`generateBranchConfig`) — so on a grouped branch
# the task runs or does not run depending on alphabetical order.
#
# Every file this target may rewrite has to be listed in the `fileFilters` of
# renovate.json's postUpgradeTasks: whatever is missing there is discarded from
# the Renovate branch.

FRONTEND_DIR := custom_components/selora_ai/frontend

.PHONY: generate
generate:
	cd $(FRONTEND_DIR) && npm ci && npm run build
