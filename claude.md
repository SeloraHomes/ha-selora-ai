# Selora AI — Home Assistant Integration

> This file is read by AI coding assistants (Claude Code, Zencoder, Copilot, etc.)
> to maintain consistency across developers and models. Keep it up to date.

## What This Is

A custom Home Assistant integration (`custom_components/selora_ai/`) that acts as a "smart butler":
- Analyzes device states and usage patterns via LLM (Anthropic Claude or local Ollama)
- Auto-generates HA automations (disabled, prefixed `[Selora AI]` for user review)
- Accepts natural language commands via webhook and translates them to HA service calls
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
├── __init__.py          # Integration setup/teardown, webhooks, entry routing
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
└── brand/               # Logo and icon assets
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
- Webhook endpoints support both POST and GET:
  - `/api/webhook/selora_ai_command` — natural language commands
  - `/api/webhook/selora_ai_devices` — device management

### Config Flow
- First entry: LLM provider selection → credentials → device discovery → area assignment → results
- Subsequent "Add Entry": skips LLM config, goes straight to device discovery
- Anthropic step shows a form for the user's API key (never auto-configure)
- `strings.json` and `translations/en.json` must always stay in sync
- Step IDs must match keys in strings.json: `user`, `anthropic`, `ollama`, `select_devices`, `results`

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

## Testing Webhooks

```bash
# POST (programmatic)
curl -X POST http://localhost:8123/api/webhook/selora_ai_command \
  -H 'Content-Type: application/json' \
  -d '{"command": "turn on the kitchen tv"}'

# GET (browser-clickable)
http://localhost:8123/api/webhook/selora_ai_command?command=turn+on+the+kitchen+tv
```

## LLM Providers

| Provider | Config Key | Default Model | Notes |
|----------|-----------|---------------|-------|
| Anthropic | `anthropic_api_key` + `anthropic_model` | `claude-sonnet-4-20250514` | Cloud, recommended |
| Ollama | `ollama_host` + `ollama_model` | `llama3.1` at `localhost:11434` | Local, no data leaves network |