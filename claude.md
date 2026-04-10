# Selora AI — Home Assistant Integration

> This file is read by AI coding assistants (Claude Code, Zencoder, Copilot, etc.)
> to maintain consistency across developers and models. Keep it up to date.

## What This Is

A custom Home Assistant integration (`custom_components/selora_ai/`) that acts as a "smart butler":
- Analyzes device states and usage patterns via LLM (Anthropic Claude or local Ollama)
- Auto-generates HA automations (disabled, prefixed `[Selora AI]` for user review)
- Accepts natural language commands via the Selora panel and Home Assistant Assist
- Discovers and onboards network devices during initial setup

## Architecture

```
HA entity registry / state machine / recorder (SQLite)
    |
    v
DataCollector  ──snapshot──>  LLMClient (Anthropic API or local Ollama)
    |                              |
    |                         suggestions
    v                              v
logging + sensors         automations.yaml (disabled) + reload
```

## Project Structure

```
custom_components/selora_ai/
├── __init__.py          # Integration setup/teardown, entry routing
├── config_flow.py       # UI config flow (LLM setup → device discovery → area assignment → results)
├── collector.py         # Hourly data collection + LLM automation writer
├── llm_client.py        # Unified LLM client (Anthropic + Ollama)
├── device_manager.py    # Device discovery, pairing, area assignment, dashboard generation
├── button.py            # Hub action buttons (Discover, Scan, Cleanup, Reset)
├── sensor.py            # Hub sensors (Status, Devices, Discovery, Last Activity)
├── const.py             # Constants, config keys, known integrations database
├── manifest.json        # HA integration manifest
├── strings.json         # UI strings for config flow
├── translations/en.json # English translations (must match strings.json)
├── brand/               # Logo and icon assets
└── frontend/
    └── src/
        ├── panel.js                  # LitElement host (properties, lifecycle, render dispatch)
        └── panel/
            ├── render-automations.js # Automation list, cards, flowchart, unavailable modal
            ├── render-chat.js        # Chat messages, YAML editor, new-automation dialog
            ├── render-settings.js    # Settings tab
            ├── render-suggestions.js # Suggestion cards
            ├── render-version-history.js # Version history drawer + diff viewer
            ├── stale-automations.js  # Stale detection helpers + stale modal/detail
            ├── automation-crud.js    # CRUD websocket calls
            ├── automation-management.js # Bulk edit, enable/disable, filter
            ├── session-actions.js    # Session list actions
            ├── suggestion-actions.js # Accept/dismiss/snooze suggestion actions
            ├── chat-actions.js       # Send message, streaming
            └── styles/               # CSS-in-JS style modules
```

## Key Conventions

### Code Style
- Python 3.12+, async/await throughout
- `from __future__ import annotations` in every file
- Type hints using modern syntax (`str | None`, not `Optional[str]`)
- Logging via `_LOGGER = logging.getLogger(__name__)`
- No hardcoded secrets — API keys come from user config entry, never from constants

### Home Assistant Patterns
- Config entries have an `entry_type` field: `"llm_config"` or `"device_onboarding"`
- Entity platforms: `sensor`, `button` (registered in `PLATFORMS` list in `__init__.py`)
- All entities use `_attr_has_entity_name = True` and reference the hub device `(DOMAIN, "selora_ai_hub")`
- Dispatcher signals for real-time updates: `SIGNAL_DEVICES_UPDATED`, `SIGNAL_ACTIVITY_LOG`
- Dashboard generation uses HA's Lovelace API (`LovelaceStorage.async_save`), not direct file writes

### Config Flow
- First entry: LLM provider selection → credentials → device discovery → area assignment → results
- Subsequent "Add Entry": skips LLM config, goes straight to device discovery
- Anthropic step shows a form for the user's API key (never auto-configure)
- `strings.json` and `translations/en.json` must always stay in sync
- Step IDs must match keys in strings.json: `user`, `anthropic`, `ollama`, `select_devices`, `results`

### Frontend File Organization
- `panel.js` is the LitElement host — it owns properties, lifecycle, and render dispatch only. Do not add feature logic or templates here.
- Each tab/feature has its own `render-*.js` file under `panel/`. New features (modals, sections, views) go in dedicated files, not appended to existing render files.
- Action helpers (websocket calls, state mutations) go in `*-actions.js` or `*-crud.js` files, not inline in templates.
- Configurable values (like stale days threshold) should come from `host._config` (populated via websocket), not hardcoded as JS constants. This keeps the backend `const.py` as the single source of truth.
- Keep individual `panel/` files under ~400 lines. If a file grows past that, split the new feature into its own module.
- Run `node build.js` from `frontend/` after any source change — the bundled `panel.js` is committed.

### Git & Branching
- Main branch: `main`
- Feature branches: `selora-ai-<feature>`
- Commit messages: conventional commits (`feat:`, `fix:`, `refactor:`, `docs:`)
- Never commit secrets — `.env` and `secrets.yaml` are in `.gitignore`
- GitLab CI runs SAST and secret detection — all findings must be resolved before merge

### What NOT to Do
- Do not hardcode API keys or tokens anywhere
- Do not use `hashlib.md5` — use `uuid.uuid4()` for unique IDs (SAST flags md5 as weak crypto)
- Do not use bare `except Exception` — catch specific exceptions
- Do not auto-accept discovered devices without user consent
- Do not write to Lovelace files directly — use the HA Lovelace API
- Do not add `field` from dataclasses unless actually used
- Do not break the config flow step → strings.json mapping

## Testing

### Python (pytest)

```bash
# Create venv and install deps
uv venv .venv --python 3.13
source .venv/bin/activate
uv pip install pytest pytest-asyncio pytest-homeassistant-custom-component "ruamel.yaml>=0.18" anthropic home-assistant-intents

# Run all tests
pytest tests/ -v

# Run a single file
pytest tests/test_automation_utils.py -v
```

Tests live in `tests/` and cover:
- `test_automation_utils.py` — validation, risk assessment, YAML I/O, async CRUD
- `test_automation_store.py` — versioning, lifecycle, drafts
- `test_pattern_engine.py` — time, correlation, sequence detectors
- `test_pattern_store.py` — ring buffer, pattern/suggestion persistence
- `test_suggestion_generator.py` — pattern→automation conversion
- `test_config_flow.py` — multi-step config flow routing
- `test_sensor.py` — sensor helper functions
- `test_conversation.py` — HA Assist entity fallbacks

### JavaScript (Vitest)

```bash
cd custom_components/selora_ai/frontend
npm ci
npm test          # vitest run
npm run test:watch  # vitest (watch mode)
```

JS tests cover shared utilities in `src/shared/__tests__/`:
- `date-utils.test.js` — relative time formatting
- `formatting.test.js` — entity/state/duration formatting
- `flow-description.test.js` — trigger/condition/action descriptions
- `markdown.test.js` — markdown rendering, automation block stripping

### CI

GitLab CI runs both test suites in the `test` stage (`unit` + `frontend` jobs).
GitHub Actions runs HACS validation and hassfest (manifest/strings/translations).
Lefthook runs tests, lint, and validation on `pre-push` locally (including hassfest via Docker).

## Deploying to Dev

`just deploy` builds the frontend and syncs files to a dev HA instance over SSH, then restarts HA.
`just deploy-no-restart` does the same without restarting.

### Prerequisites

1. Install the **Advanced SSH & Web Terminal** add-on in HA (Settings → Add-ons)
2. In the add-on configuration, add your SSH public key and enable SFTP
3. Copy `.env.example` to `.env` and set `HA_HOST` to your HA instance (e.g. `root@192.168.x.x`)

```bash
cp .env.example .env
# Edit .env with your HA IP address

just deploy            # build + sync + restart
just deploy-no-restart # build + sync only
```

> Use the IP address rather than `homeassistant.local` — mDNS resolution adds latency on every SSH/SCP connection.

## Running Locally

```bash
# Docker (recommended)
docker compose up -d

# Or bare metal
python3 -m venv venv && source venv/bin/activate
pip install homeassistant
hass -c .
```

Open http://localhost:8123, add the Selora AI integration under Settings > Devices & Services.

## LLM Providers

| Provider | Config Key | Default Model | Notes |
|----------|-----------|---------------|-------|
| Anthropic | `anthropic_api_key` + `anthropic_model` | `claude-sonnet-4-6` | Cloud, recommended |
| Ollama | `ollama_host` + `ollama_model` | `llama3.1` at `localhost:11434` | Local, no data leaves network |
