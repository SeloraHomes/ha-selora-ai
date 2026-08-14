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
# Why a dependency bump invalidates anything here: Lit ships *inside* panel.js,
# so a lockfile-only change rewrites the bundle — and with it the build id,
# which build-id.js derives from the built bundle's own bytes — without touching
# a line of src/. The `frontend` CI job fails the MR when the committed bundle
# and a fresh build disagree.
#
# Every file this target may rewrite has to be listed in the `fileFilters` of
# renovate.json's postUpgradeTasks: whatever is missing there is discarded from
# the Renovate branch. That is more than the bundle. `npm run build` runs
# `prettier --write` over src/**/*.js, build.js and postbuild.js before it
# bundles, so dropping those reformats leaves the next prettier bump failing
# `lint:prettier` against sources Renovate was not allowed to fix.

FRONTEND_DIR := custom_components/selora_ai/frontend

.PHONY: generate
generate:
	cd $(FRONTEND_DIR) && npm ci && npm run build
