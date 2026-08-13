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
# Why a dependency bump invalidates anything here: build-id.js hashes the
# frontend package.json and package-lock.json among the bundle's inputs, and Lit
# ships *inside* panel.js — so a lockfile-only change rewrites the bundle and
# moves the build id without touching a line of src/. The `frontend` CI job
# fails the MR when the committed bundle and a fresh build disagree.

FRONTEND_DIR := custom_components/selora_ai/frontend

.PHONY: generate
generate:
	cd $(FRONTEND_DIR) && npm ci && npm run build
