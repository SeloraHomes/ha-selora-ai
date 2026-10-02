# Contributing to Selora AI

Thanks for working on the integration! This is a quickstart — see
[`CLAUDE.md`](CLAUDE.md) for conventions and design rules, and the
[Selora AI roadmap](https://selorahomes.com/docs/roadmap/) for planned features.

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

Or bare metal:

```bash
python3 -m venv venv && source venv/bin/activate
pip install homeassistant
hass -c .
```

## Running the tests

```bash
uv venv .venv --python 3.14
source .venv/bin/activate
uv pip install pytest pytest-asyncio "pytest-homeassistant-custom-component<0.13.358" "ruamel.yaml>=0.18" anthropic home-assistant-intents "rapidfuzz>=3.0"
pytest tests/

cd custom_components/selora_ai/frontend && npm ci && npm test
```

## Deploying to a dev Home Assistant

1. Install the **Advanced SSH & Web Terminal** add-on (Settings → Add-ons), add
   your SSH public key in its configuration, and enable SFTP.
2. `cp .env.example .env` and set `HA_HOST` (e.g. `root@192.168.x.x`). Use the IP
   rather than `homeassistant.local` — mDNS adds latency to every connection.
3. `just deploy` builds the frontend, syncs the files and restarts HA.
   `just deploy-no-restart` skips the restart, which is only safe when no Python
   changed: Python modules stay loaded while the panel bundle is served fresh,
   and the panel shows a restart banner when the two disagree.

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
