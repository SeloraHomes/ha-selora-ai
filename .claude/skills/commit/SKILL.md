---
name: commit
description: Create a git commit following the project's conventional commit rules
---

# Commit

Create a git commit for staged and unstaged changes.

## Commit message format

This project uses commitlint with `@commitlint/config-conventional`, enforced by a lefthook `commit-msg` hook.

Rules:
- Format: `<type>: <subject>` (no scope required)
- **Subject: max 50 characters total** (including `type: ` prefix), lowercase, no trailing period
- Body (optional): wrap at 72 characters, separated from subject by a blank line

## Choosing the right commit type

This project uses semantic-release. Some types **trigger a release** (new version published to users), others do not. Choose carefully — a wrong type means an unnecessary release.

**Release-triggering types** — only use when the change is meaningful to end users:
- `feat:` → minor release — a new user-facing feature
- `fix:` → patch release — a bug fix that affects user behavior
- `perf:` → patch release — a performance improvement users would notice
- `refactor:` → patch release — use only if it changes observable behavior; otherwise use `chore:`
- `revert:` → patch release — reverting a user-facing change

**Non-release types** — use for everything else:
- `chore:` → no release — tooling, CI, dependencies, config, internal cleanup
- `docs:` → no release — documentation changes
- `style:` → no release — formatting, whitespace, linting fixes
- `test:` → no release — adding or fixing tests
- `ci:` → no release — CI/CD pipeline changes
- `build:` → no release — build system changes

**Rule of thumb**: if the change doesn't affect what end users experience when running the integration, it should NOT trigger a release. Use `chore:`, `docs:`, `style:`, `test:`, `ci:`, or `build:` instead.

Examples:
- Fixing lefthook config → `chore:` (not `fix:` — this is internal tooling)
- Updating internal doc references → `docs:` (not `fix:`)
- Optimizing CI/hook parallelism → `chore:` (not `perf:` — users don't see this)
- Fixing a bug in device discovery → `fix:` (users are affected)
- Adding a new sensor entity → `feat:` (users get new functionality)

## Steps

1. Run `git status` and `git diff` to review all changes
2. Run `git log --oneline -5` to match the existing commit style
3. Draft a commit message following the rules above — count characters to ensure the subject line fits within 50
4. Stage relevant files by name (avoid `git add -A`)
5. Commit with the message using a HEREDOC
6. If the commit-msg hook fails, fix the message and create a **new** commit (do not amend)
