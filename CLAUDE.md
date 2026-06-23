# Selora AI — Home Assistant Integration

> This file is read by AI coding assistants (Claude Code, Zencoder, Copilot, etc.)
> to maintain consistency across developers and models. Keep it up to date.

## What This Is

A custom Home Assistant integration (`custom_components/selora_ai/`) that acts as a "smart butler":
- Analyzes device states and usage patterns via LLM (Anthropic Claude, Google Gemini, OpenAI, or local Ollama)
- Auto-generates HA automations (disabled, prefixed `[Selora AI]` for user review)
- Accepts natural language commands via the Selora panel and Home Assistant Assist
- Discovers and onboards network devices during initial setup

## Architecture

```
HA entity registry / state machine / recorder (SQLite)
    |
    v
DataCollector  ──snapshot──>  LLMClient (Anthropic / Gemini / OpenAI / Ollama)
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
├── llm_client.py        # Business-logic LLM facade (prompts, parsing, tool orchestration)
├── providers/           # Pluggable LLM backends (Anthropic, Gemini, OpenAI, Ollama)
├── device_manager.py    # Device discovery, pairing, area assignment, dashboard generation
├── button.py            # Hub action buttons (Discover, Scan, Cleanup, Reset)
├── sensor.py            # Hub sensors (Status, Devices, Discovery, Last Activity)
├── selora_auth.py       # Multi-auth orchestration (HA token, MCP token, Selora JWT)
├── mcp_token_store.py   # Local MCP API token store (CRUD, hash-only storage)
├── telemetry.py         # Anonymous, opt-in repair-counter telemetry (PostHog)
├── types.py             # Shared TypedDict definitions (automations, patterns, suggestions, etc.)
├── const.py             # Constants, config keys, known integrations database
├── manifest.json        # HA integration manifest
├── strings.json         # UI strings for config flow
├── translations/         # HA-side translations (en, fr, de, es, it, nl, hu, pt, ru, ja, ko, zh-Hans, zh-Hant) — all keys must match strings.json
├── brand/               # Logo and icon assets
└── frontend/
    └── src/
        ├── panel.js                  # LitElement host (properties, lifecycle, render dispatch)
        └── panel/
            ├── render-automations.js # Automation list, cards, flowchart, unavailable modal
            ├── render-chat.js        # Chat messages, YAML editor, new-automation dialog
            ├── render-settings.js    # Settings tab
            ├── render-telemetry-consent.js # One-time telemetry opt-in banner
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
- Python 3.14+, async/await throughout
- `from __future__ import annotations` in every file
- **Fully typed**: every function/method must have parameter and return type annotations
- Type hints using modern syntax (`str | None`, not `Optional[str]`)
- Use TypedDicts from `types.py` instead of `dict[str, Any]` for known data structures (automations, patterns, suggestions, snapshots, etc.)
- Import types under `TYPE_CHECKING` guard when only needed for annotations
- Avoid bare `Any` — use concrete types or TypedDicts. `Any` is acceptable only for truly dynamic data (e.g. raw JSON from external APIs, HA store loads)
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

### i18n / Translations
- Backend (config flow, entity names, errors): HA standard. `strings.json` is the source of truth (English). `translations/<lang>.json` mirrors its structure for each supported locale.
- Supported locales: `en`, `fr`, `de`, `es`, `it`, `nl`, `hu`, `pt`, `ru`, `ja`, `ko`, `zh-Hans`, `zh-Hant`. When adding a string to `strings.json`, add the same key to ALL `translations/*.json` files in the same commit. Hassfest fails CI if any locale is missing a key.
- Preserve placeholders (`{count}`, `{device_list}`, `{succeeded}`, `{failed}`, `{needs_attention}`, `{details}`) verbatim across all locales.
- Conversational LLM replies (chat/Assist) follow `hass.config.language`, not the UI locale set above. `_language_directive()` in `llm_client/prompts.py` injects a "respond in <language>" instruction. `_LANGUAGE_NAMES` is the allowlist of recognized codes — unknown/untrusted codes are dropped (no directive), never echoed into the system prompt. To add a conversational language, add its base code → English name to `_LANGUAGE_NAMES`.
- Runtime confirmation/approval strings (chat command results) are NOT in `translations/*.json` — they live in per-language dicts keyed by base code: `_PAST_VERBS_*` / `_GENERIC_RAN_BY_LANG` / `_DONE_BY_LANG` / `_SENTENCE_FORMAT_BY_LANG` in `llm_client/command_policy.py`, `_CANNED_*` in `llm_client/client.py`, and `_APPROVAL_*_BY_LANG` in `__init__.py`. All fall back to English when a key is missing. `_normalize_lang()` strips the region subtag (`zh-Hant` → `zh`), so script variants share one runtime entry — `zh-Hant` UI users get Simplified runtime confirmation verbs until region-aware normalization is added.
- Frontend (panel) i18n is partially wired: a `_t()` helper exists in `frontend/src/panel.js` and resolves keys under the `component.selora_ai.common.*` namespace via `hass.localize()`. Today only the feedback modal uses it (~19 calls out of ~5,000 user-facing literals — see `frontend/src/panel/*.js`, `frontend/src/shared/*.js`).
- When touching frontend UI text: prefer `_t('key')` over hardcoded strings. Add the key to `common` in `strings.json` AND every `translations/*.json` locale. Use ICU-style placeholders for interpolation.
- Bulk frontend extraction is a separate ongoing effort — do not block small PRs on it, but do not introduce new hardcoded literals when an existing key fits.

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
- Do not use `dict[str, Any]` for data structures that have a TypedDict in `types.py` — import and use the TypedDict
- Do not add untyped functions — every new function/method must have full parameter and return type annotations

## Testing

### Python (pytest)

```bash
# Create venv and install deps
uv venv .venv --python 3.14
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
- `test_selora_auth.py` — JWT validation, dual/multi-auth, MCP token auth path
- `test_mcp_token_store.py` — token CRUD, hash validation, expiry, revocation
- `test_telemetry.py` — opt-in gating, payload allowlist (no PII), dedup, install-id, error-swallowing

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

## Telemetry

`telemetry.py` emits **anonymous, opt-in** product telemetry — three event types, all counter/enum/version-only:

- `home_snapshot` — inventory counts of the install (devices, integrations, automations, scenes, scripts, blueprints, areas, entities), device-count-per-integration, Selora-generated automation count, accepted/dismissed suggestion counts, LLM provider, HA + integration versions. Point-in-time *gauges*. Sent once ~2 min after startup (so registries are populated), then every 24h. Scheduled in `async_setup_entry`; timer unsub stored as `unsub_telemetry` and cleaned up in `async_unload_entry`.
- `llm_output_repaired` — counts how often each safety-net repair on raw LLM output fires (see `REPAIR_TYPES`), broken down by provider/model, to measure repair effectiveness across model versions.
- `usage_activity` — period *deltas* of how the install is used: automations created/refined/deleted/enabled/disabled, scenes created, patterns detected, suggestions generated/accepted/dismissed/snoozed, chat messages + sessions, Assist queries, commands executed, devices paired, discoveries run, LLM call + input/output token totals, quota-exceeded events. Counters live in memory on the `TelemetryClient` (`record_activity`, allowlist `_ACTIVITY_COUNTER_KEYS`), accumulate regardless of opt-in, and are flushed-then-reset by `async_send_activity` on the recurring 24h tick (`_telemetry_periodic` in `async_setup_entry` — the startup tick sends snapshot only, so `period_hours` stays accurate). **Not persisted** — restart drops the partial window (acceptable for anonymous trend data; avoids write amplification on the hot chat path).

Shared rules:

- **Opt-in, off by default.** Gated on the `telemetry_enabled` toggle (Settings → Advanced). Read live on every emit (`CONF_TELEMETRY_ENABLED` is in `hot_option_keys`, so flipping it needs no reload). Distinct from the *local-only* cost tracking in `usage.py` / `usage_store.py`, which never leaves the network.
- **Consent:** a one-time dismissible banner (`render-telemetry-consent.js`) shows atop the panel until the user picks Enable / No thanks. The choice sets `telemetry_prompt_seen` (a `frontend_only_keys` option, no reload) so it never re-nags; the Settings toggle remains the way to change it later.
- **Local model names are never sent.** `_safe_model` replaces the model id with `"local"` for `ollama` / `selora_local` (user-named, potentially identifying); cloud providers send their public catalog model id.
- **Payloads are counters/enums/versions only.** Per-event allowlists `_REPAIR_PROPERTY_KEYS` / `_SNAPSHOT_PROPERTY_KEYS` / `_ACTIVITY_PROPERTY_KEYS` are enforced in `_capture` before every POST. **Never** entity ids, friendly names, prompt text, or response text. (`devices_by_integration` keys are HA integration domain names like `zha`/`hue` — public identifiers, just counts.)
- **Identity:** `distinct_id` is a random per-install UUID stored locally (`{DOMAIN}.telemetry`) with no link to household/network/account. Every POST also sets `$ip: "0.0.0.0"` + `$geoip_disable: true` so PostHog never stores/geolocates the host's real IP (anonymity holds regardless of project settings).
- **Transport:** direct POST to PostHog via `async_get_clientsession` (no SDK dependency). The PostHog project key in `const.py` is a publishable write-only ingest token — public by design, not a secret. Endpoint overridable via `CONF_TELEMETRY_ENDPOINT`.
- **How repairs are recorded:** pure helpers call `record_repair("<type>")` (a no-op outside an LLM call). `UsageTracker.scope` opens a per-call ContextVar buffer and drains it at the call boundary, where provider/model are known, then POSTs fire-and-forget (never raises). To add a repair counter: add the type to `REPAIR_TYPES`, call `record_repair(...)` at the repair site, cover it in `tests/test_telemetry.py`. The five instrumented paths: `service_name_inference`, `state_info_strip`, `trailing_marker_reposition`, `friendly_name_strip` (`llm_client/parsers.py`, `command_policy.py`), `qwen_normalize` (`providers/_qwen_repair.py`).
- To add a snapshot count: add the key to `_SNAPSHOT_PROPERTY_KEYS` and populate it in `TelemetryClient._gather_snapshot`.
- To add an activity counter: add the name to `_ACTIVITY_COUNTER_KEYS`, call `record_activity(hass, "<name>"[, n])` at the action's chokepoint (instrumented sites today: `automation_store.add_version`/`purge_record`, `automation_utils.async_toggle_automation`, `scene_store.async_add_scene`, `pattern_store.save_pattern`/`save_suggestion`/`update_suggestion_status`, the chat/command handlers + `_execute_command_calls` in `__init__.py`, `conversation._async_handle_message`, `device_manager.discover_network_devices`/`_count_if_paired`, `llm_client/usage.flush`, `providers/base._emit_quota_exceeded`), and cover it in `tests/test_telemetry.py`. The helper never raises and counts even when opted out (the flush is what's gated).

## LLM Providers

| Provider | Config Key | Default Model | Notes |
|----------|-----------|---------------|-------|
| Anthropic | `anthropic_api_key` + `anthropic_model` | `claude-sonnet-4-6` | Cloud, recommended |
| Google Gemini | `gemini_api_key` + `gemini_model` | `gemini-2.5-flash` | Cloud, uses native REST API (not OpenAI-compat) |
| OpenAI | `openai_api_key` + `openai_model` | `gpt-5.4` | Cloud, OpenAI chat completions format |
| Ollama | `ollama_host` + `ollama_model` | `llama4` at `localhost:11434` | Local, no data leaves network |
