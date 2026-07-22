# Contributing to Selora AI

Thanks for working on the integration! This is a quickstart — see
[`CLAUDE.md`](CLAUDE.md) for the full architecture, conventions, and testing
details, and the [Selora AI roadmap](https://selorahomes.com/docs/roadmap/) for
planned features.

## Where this repo lives

- **[GitLab](https://gitlab.com/selorahomes/products/selora-ai/ha-integration/) is canonical.** All development happens here, and we accept **Merge Requests** here.
- **[GitHub](https://github.com/SeloraHomes/ha-selora-ai/) is a read-only mirror** (it's what HACS distributes from). For technical reasons related to the mirroring, we can't act on **Pull Requests** opened on GitHub — please open a Merge Request on GitLab instead. GitHub issues are fine.

## Development setup

```bash
docker compose up -d
```

Open http://localhost:8123 and add Selora AI under **Settings → Devices &
Services**. If running Ollama alongside Docker, use
`http://host.docker.internal:11434` as the Ollama host.

## Before you push

Install [Lefthook](https://github.com/evilmartians/lefthook) once so the same
checks that run in CI run locally (requires Docker for hassfest + commitlint):

```bash
brew install lefthook   # or: npm install -g @evilmartians/lefthook
lefthook install
```

`pre-push` runs the full suite (ruff, pytest, frontend tests + build,
HACS/manifest/hassfest validation). Auto-fix lint and formatting with:

```bash
ruff check --fix custom_components/
ruff format custom_components/
```

## Commits & branching

- Branch off `main` as `selora-ai-<feature>`.
- Use [Conventional Commits](https://www.conventionalcommits.org/)
  (`feat:`, `fix:`, `refactor:`, `docs:`, …) — commitlint enforces this.
- Releases are automated: semantic-release cuts the version, changelog, and tag
  on every push to `main`. Never bump `manifest.json` or edit `CHANGELOG.md` by
  hand.
- Never commit secrets. GitLab CI runs SAST and secret detection.

Open a merge request against `main`; all CI jobs must pass before merge.
