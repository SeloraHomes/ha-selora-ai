# Selora AI — Home Assistant Integration

> This file is read by AI coding assistants (Claude Code, Zencoder, Copilot, etc.)
> to maintain consistency across developers and models. Keep it up to date.

## What This Is

A custom Home Assistant integration (`custom_components/selora_ai/`) that acts as a "smart butler":
- Analyzes device states and usage patterns via LLM — **Selora AI** (Selora Cloud, or the on-device **Selora AI Local** model), Anthropic Claude, Google Gemini, OpenAI, OpenRouter, or local Ollama
- Auto-generates HA automations (disabled, prefixed `[Selora AI]` for user review)
- Accepts natural language commands via the Selora panel and Home Assistant Assist
- Discovers and onboards network devices during initial setup

## Architecture

```
HA entity registry / state machine / recorder (SQLite)
    |
    v
DataCollector  ──snapshot──>  LLMClient (Selora Cloud / Selora Local / Anthropic / Gemini / OpenAI / OpenRouter / Ollama)
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
├── llm_client/          # LLM facade package (client, prompts, parsers, intent, command_policy, lang_detect, state_filter, usage)
├── providers/           # Pluggable LLM backends (Selora Cloud/Local, Anthropic, Gemini, OpenAI, OpenRouter, Ollama)
├── device_manager.py    # Device discovery, pairing, area assignment, dashboard generation
├── conversation.py      # Assist Conversation Agent — routes natural language to HA service calls
├── automation_utils.py  # Validation, risk assessment, YAML I/O, async automation CRUD
├── automation_store.py  # Lifecycle + versioning for [Selora AI] automations
├── group_manager.py     # HA group-helper CRUD (drives HA's own `group` config flow)
├── dashboard_manager.py # Lovelace view/card read + edit (chat tool surface)
├── registry_manager.py  # Area/floor/entity/device registry reads + writes, helper inventory
├── script_manager.py    # scripts.yaml CRUD (mapping, not a list — unlike automations)
├── label_manager.py     # Label registry + delta assignment across entities/devices/areas
├── diagnostics_tools.py # Read-only: system_log errors, automation run traces
├── code_stamp.py        # Source-signature skew detection (restart-required handshake)
├── scene_store.py       # Scene creation + persistence
├── websocket/           # Panel websocket handlers, one module per domain (registered lazily)
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
            ├── render-stale-code-notice.js  # Restart/reload-required banner
            ├── version-actions.js    # Code-skew handshake (unknown command ⇒ restart)
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
- Selora Cloud has no credentials step — OAuth linking happens post-setup from the panel
- `strings.json` and `translations/en.json` must always stay in sync
- Step IDs must match keys in strings.json: `user`, `selora_local`, `selora_cloud`, `anthropic`, `openai`, `gemini`, `openrouter`, `ollama`, `select_devices`, `results`

### i18n / Translations
- Backend (config flow, entity names, errors): HA standard. `strings.json` is the source of truth (English). `translations/<lang>.json` mirrors its structure for each supported locale.
- Supported locales: `en`, `fr`, `de`, `es`, `it`, `nl`, `hu`, `pt`, `ru`, `ja`, `ko`, `zh-Hans`, `zh-Hant`. When adding a string to `strings.json`, add the same key to ALL `translations/*.json` files in the same commit. Hassfest fails CI if any locale is missing a key.
- Preserve placeholders (`{count}`, `{device_list}`, `{succeeded}`, `{failed}`, `{needs_attention}`, `{details}`) verbatim across all locales.
- Conversational reply language is resolved per turn by `resolve_reply_language()` in `llm_client/lang_detect.py`: **detected message language** (over the shipped fr/de/es/it locales, no external dep) first, then the panel-sent locale, then `hass.config.language`. This drives BOTH the LLM `_language_directive()` and the deterministic command-confirmation builder, so a French command on an English-UI install gets a French confirmation. `architect_chat` / `architect_chat_stream` reassign their local `language` to this resolved value at entry, so all downstream uses agree. Detection returns `None` for English and for unsupported scripts (Japanese, etc.) → those fall back to the panel locale (and the non-English guard still refuses genuinely-foreign input it can't converse in). To add a detectable language, add a high-signal marker set to `_MARKERS` in `lang_detect.py`.
- Status questions ("which lights are on?", "quelles lumières sont éteintes?") get a deterministic answer set on the cloud path: `state_filter.ground_truth_block()` detects the question multilingually (interrogative + category word + state word), computes the exact matching entity_ids from the snapshot's live state, and injects a GROUND TRUTH constraint into the prompt (`_build_chat_messages`). The LLM phrases the reply in the user's language but the SET and COUNT are fixed by code — fixes wrong counts, wrong-domain devices (fans in a lights answer), and EN/FR set drift. Detection requires an interrogative so it never hijacks an imperative command. The local provider has its own equivalent (`_maybe_state_filter_envelope`, English-only) — that path is separate.
- `_language_directive()` in `llm_client/prompts.py` injects a "respond in <language>" instruction. `_LANGUAGE_NAMES` is the allowlist of recognized codes — unknown/untrusted codes are dropped (no directive), never echoed into the system prompt. To add a conversational language, add its base code → English name to `_LANGUAGE_NAMES`.
- Runtime confirmation/approval strings (chat command results) are NOT in `translations/*.json` — they live in per-language dicts keyed by base code: `_PAST_VERBS_*` / `_GENERIC_RAN_BY_LANG` / `_DONE_BY_LANG` / `_SENTENCE_FORMAT_BY_LANG` in `llm_client/command_policy.py`, `_CANNED_*` in `llm_client/client.py`, and `_APPROVAL_*_BY_LANG` in `__init__.py`. All fall back to English when a key is missing. `_normalize_lang()` strips the region subtag (`zh-Hant` → `zh`), so script variants share one runtime entry — `zh-Hant` UI users get Simplified runtime confirmation verbs until region-aware normalization is added.
- Conversational entity filtering is multilingual. `_low_context_keywords()` in `llm_client/intent.py` tokenizes via `lexical.normalize()` (NFKC + casefold, Unicode-aware) so accented words survive whole — an ASCII `[^a-z0-9]+` split shreds them ("lumières" → "lumi"/"res"). Category words map to HA domains in `_CATEGORY_KEYWORD_TO_DOMAIN` (English) plus a merged fr/de/es/it block; keys are in `normalize()` form (casefolded, accents kept, with accent-free variants). To support a new conversational language's "which lights/covers/locks…" queries, add its category words there. `_DOMAIN_NEED_TOKENS` / `_DEVICE_CLASS_NEED_TOKENS` (cloud pinning) remain English-only.
- Streaming tool rounds must carry the model's own prose forward. `OpenAICompatibleProvider.stream_with_tools` records each text delta into `content_blocks`, and `append_streaming_tool_results` attaches the joined result as `content` on the **first** synthesized assistant message (repeating it per tool call would read as the model having said it several times). Without this the model sees its own tool calls and their results with no memory of what it said, re-orients from scratch, and re-narrates the same sentence every round. The non-streaming `append_tool_result` never had the problem — it appends the provider's whole message, `content` included.
- Frontend (panel) i18n does **not** go through `hass.localize()`. `_t(key, fallback)` in `frontend/src/panel.js` delegates to `localize()` in `frontend/src/shared/i18n.js`, which reads a catalog **compiled into the bundle**: `i18n.js` imports all 13 `translations/*.json` directly, so esbuild inlines every locale into `panel.js`. Resolution is exact locale → `en` → the inline `fallback` argument. `pickLocale()` lowercases and strips the region subtag, so `zh-Hans` is keyed `zh-hans` and an unknown locale degrades to its base code then `en`. `strings.json` is *not* imported by the frontend — the panel reads `translations/en.json`, so a key added to `strings.json` alone resolves to its fallback in every locale.
- **Editing a `translations/*.json` file changes the bundle.** Rebuild and commit `panel.js` + `panel.build.json` with the translation change, exactly as for a `src/` edit — the CI `frontend` job fails the MR when the committed bundle disagrees with a fresh build.
- The inline `fallback` is why an unbacked key still renders English instead of blank, and therefore why a missing key is silent: `_t()` never warns. Coverage today is ~942 call sites across 28 files, 853 distinct keys, of which ~46 have no entry in `common` and resolve to their fallback only.
- When touching frontend UI text: prefer `_t('key', 'English default')` over hardcoded strings. Add the key to `common` in `strings.json` AND every `translations/*.json` locale, in the same commit. Use ICU-style placeholders for interpolation. Keys are appended to `common` in feature groups, not sorted.
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
uv pip install pytest pytest-asyncio pytest-homeassistant-custom-component "ruamel.yaml>=0.18" anthropic home-assistant-intents "rapidfuzz>=3.0"

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
- `test_group_tools.py` — group-helper CRUD tools; drives HA's real `group` config flow
- `test_chat_handlers.py` — whole chat turns through `chat_harness.py`

#### The chat handler harness

`tests/chat_harness.py` drives `selora_ai/chat` and `selora_ai/chat_stream` end
to end. It stubs exactly one thing — the provider round trip
(`architect_chat` / `architect_chat_stream`) — and keeps the handler, a real
`LLMClient` (so the streaming path runs the real `parse_streamed_response` over
the scripted text), a real `ConversationStore`, and a real `automations.yaml`.

Reach for it whenever a turn's behaviour depends on more than one step. A helper
returning the right value proves nothing about a handler that then fails to pass
it to the LLM, persist it on the message, or send it to the panel — and each of
those three has shipped as a bug that helper-level tests could not see. A
`ChatTurn` exposes the three questions worth asking: `asked` (the kwargs the LLM
received), `done` (the terminal payload, whichever handler produced it), and
`await harness.messages()` (what a reopened session would load).

```python
harness = await ChatHarness.create(hass)
first = await harness.chat("turn the plug on at midnight", reply=_proposal())
await harness.save_proposal(first.done["automation_message_index"], "selora_ai_aaa")
harness.write_automations([AQUA_ENTRY])
second = await harness.chat("change the time to 7am", reply=_proposal())
assert second.done["refining_automation_id"] == "selora_ai_aaa"
```

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

GitLab CI runs lint (`ruff`, `prettier`) then the `test` stage: `validate` (HACS + manifest), `unit` (pytest on a 3.13/3.14 matrix), `soak`, and `frontend` (vitest).
GitHub Actions runs HACS validation and hassfest (manifest/strings/translations).
Lefthook runs tests, lint, and validation on `pre-push` locally (including hassfest via Docker).

## Deploying to Dev

`just deploy` builds the frontend and syncs files to a dev HA instance over SSH, then restarts HA.
`just deploy-no-restart` does the same without restarting.

> **A deploy without a restart leaves the instance in skew.** Python modules stay in
> `sys.modules` — reloading the integration re-runs setup but re-imports nothing — while the
> panel bundle is served straight off disk, so a page refresh alone gives the browser the new
> JS. The panel then calls last-deploy's websocket schemas and a new payload key comes back as
> `extra keys not allowed @ data['<key>']`. `code_stamp.py` hashes the paths and contents of every
> `*.py` in the component at import time; `selora_ai/version_status` compares that signature with
> the live one (and the panel's baked build id, from `frontend/panel.build.json`) so the panel can
> show a restart/reload banner and the log gets a warning. Contents, not mtimes: `rsync -az`
> preserves each source file's timestamp and `--delete`s removals, so a newest-mtime check misses
> real deployments. Use `just deploy` when Python changed.

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

- `home_snapshot` — inventory counts of the install (devices, integrations, automations, scenes, scripts, blueprints, areas, entities), device-count-per-integration, Selora-generated automation count, accepted/dismissed suggestion counts, LLM provider, the coarse self-declared install country (`hass.config.country`, ISO-3166 alpha-2, omitted when unset — read locally, never derived from IP since `$geoip_disable` stays on), HA + integration versions. Point-in-time *gauges*. Sent once ~2 min after startup (so registries are populated), then every 24h. Scheduled in `async_setup_entry`; timer unsub stored as `unsub_telemetry` and cleaned up in `async_unload_entry`.
- `llm_output_repaired` — counts how often each safety-net repair on raw LLM output fires (see `REPAIR_TYPES`), broken down by provider/model, to measure repair effectiveness across model versions.
- `usage_activity` — period *deltas* of how the install is used: automations created/refined/deleted/enabled/disabled, scenes created, patterns detected, suggestions generated/accepted/dismissed/snoozed, chat messages + sessions, Assist queries, commands executed, devices paired, discoveries run, LLM call + input/output token totals, quota-exceeded events. Counters live in memory on the `TelemetryClient` (`record_activity`, allowlist `_ACTIVITY_COUNTER_KEYS`), accumulate regardless of opt-in, and are flushed-then-reset by `async_send_activity` on the recurring 24h tick (`_telemetry_periodic` in `async_setup_entry` — the startup tick sends snapshot only, so `period_hours` stays accurate). **Not persisted** — restart drops the partial window (acceptable for anonymous trend data; avoids write amplification on the hot chat path).

Shared rules:

- **Opt-in, off by default.** Gated on the `telemetry_enabled` toggle (Settings → Advanced). Read live on every emit (`CONF_TELEMETRY_ENABLED` is in `hot_option_keys`, so flipping it needs no reload). Distinct from the *local-only* cost tracking in `usage.py` / `usage_store.py`, which never leaves the network.
- **Consent:** a one-time dismissible banner (`render-telemetry-consent.js`) shows atop the panel until the user picks Enable / No thanks. The choice sets `telemetry_prompt_seen` (a `frontend_only_keys` option, no reload) so it never re-nags; the Settings toggle remains the way to change it later.
- **Local model names are never sent.** `_safe_model` replaces the model id with `"local"` for `ollama` / `selora_local` (user-named, potentially identifying); cloud providers send their public catalog model id.
- **Payloads are counters/enums/versions only.** Per-event allowlists `_REPAIR_PROPERTY_KEYS` / `_SNAPSHOT_PROPERTY_KEYS` / `_ACTIVITY_PROPERTY_KEYS` are enforced in `_capture` before every POST. **Never** entity ids, friendly names, prompt text, or response text. (`devices_by_integration` keys are HA integration domain names like `zha`/`hue` — public identifiers, just counts.)
- **Identity:** `distinct_id` is a random per-install UUID stored locally (`{DOMAIN}.telemetry`) with no link to household/network/account. Every POST also sets `$ip: "0.0.0.0"` + `$geoip_disable: true` so PostHog never stores/geolocates the host's real IP (anonymity holds regardless of project settings).
- **Transport:** direct POST to PostHog via `async_get_clientsession` (no SDK dependency). The PostHog project key in `const.py` is a publishable write-only ingest token — public by design, not a secret. Endpoint overridable via `CONF_TELEMETRY_ENDPOINT`.
- **How repairs are recorded:** pure helpers call `record_repair("<type>")` (a no-op outside an LLM call). `UsageTracker.scope` opens a per-call ContextVar buffer and drains it at the call boundary, where provider/model are known, then POSTs fire-and-forget (never raises). To add a repair counter: add the type to `REPAIR_TYPES`, call `record_repair(...)` at the repair site, cover it in `tests/test_telemetry.py`. The instrumented paths: `service_name_inference`, `state_info_strip`, `trailing_marker_reposition`, `friendly_name_strip`, `tool_markup_leak` (`llm_client/parsers.py`, `command_policy.py`), `qwen_normalize` (`providers/_qwen_repair.py`), `cloud_json_salvage`. `tool_markup_leak` fires when a model emits its tool-call syntax as plain text (`<invoke …>` / mangled `<｜DSML｜…`) instead of a real tool_use block — stripped non-streaming by `strip_leaked_tool_markup` and mid-stream by `MarkupLeakGuard` in `llm_client/parsers.py`, both called from the tool loop in `llm_client/client.py`.
  - **The delimiter is not always ASCII.** DeepSeek fences its special tokens with U+FF5C FULLWIDTH VERTICAL LINE (`<｜tool▁calls▁begin｜>`) and writes U+2581 where the token name has an underscore. Both render as a pipe with padding, so a leak looks ASCII in a bug report while matching none of an ASCII-only pattern. `_LEAK_PIPE` / `_LEAK_SEP` carry the character classes; `_leak_marker_prefix_could_match` folds U+2581 to `_` so a half-arrived `tool▁ca` is still held back mid-stream. A new vendor's fencing character goes in those two classes, and the fixtures in `tests/test_tool_markup_leak.py` must be built from codepoints — an editor that normalizes the glyph silently turns the regression test back into the ASCII case that already passed.
  - **A leak is a failed tool call, not a final answer.** No provider parses a text-form call, so `extract_tool_calls` / `stream_with_tools` return nothing and both loops would otherwise read the round as a committed answer and return — ending the turn mid-investigation with rounds unspent, which is what makes a user type "continue". Both loops instead detect the shape (`leak_guard.suppressed`, or `strip_leaked_tool_markup` having changed the text), append the stripped prose plus `_LEAK_RETRY_DIRECTIVE`, and `continue`. Bounded by `_MAX_LEAK_RETRIES` (2) *independently* of the round budget, so a model that cannot do structured calls at all does not spend all eight rounds on retries. This is the model-feedback shape rather than parsing one vendor's text syntax back into calls.
- To add a snapshot count: add the key to `_SNAPSHOT_PROPERTY_KEYS` and populate it in `TelemetryClient._gather_snapshot`.
- To add an activity counter: add the name to `_ACTIVITY_COUNTER_KEYS`, call `record_activity(hass, "<name>"[, n])` at the action's chokepoint (instrumented sites today: `automation_store.add_version`/`purge_record`, `automation_utils.async_toggle_automation`, `scene_store.async_add_scene`, `pattern_store.save_pattern`/`save_suggestion`/`update_suggestion_status`, the chat/command handlers + `_execute_command_calls` in `__init__.py`, `conversation._async_handle_message`, `device_manager.discover_network_devices`/`_count_if_paired`, `llm_client/usage.flush`, `providers/base._emit_quota_exceeded`), and cover it in `tests/test_telemetry.py`. The helper never raises and counts even when opted out (the flush is what's gated).

## Chat automation proposals

A proposal is written when the user taps **Accept & Save** on the card, and the
panel decides between create and update from one value: `refining_automation_id`
(`_getRefiningAutomationId` in `automation-crud.js`, which also drives the
card's diff preview). Both chat handlers resolve it **before** appending the
assistant message and pass it to `append_message`, so it rides the turn's
`done` payload *and* persists on the proposal. Persisting is not belt and
braces: the target is inferred from session state at generation time and
nothing recomputes it at accept time, so a session reopened before the card is
tapped — a reload, or just revisiting it from the sidebar — resolved to nothing
and took the create path, writing the duplicate. It is stored under the key the
panel already reads, so nothing in the bundle changes.

- **A follow-up change must not become a second automation.** "Change the time
  to 7am" after an accepted card comes back as an ordinary proposal, so
  accepting it would write a second `automations.yaml` entry under the same
  alias. HA loads both, both run, the health check reports the pair, and the
  user is left deleting one by hand. `_resolve_proposal_write_target` takes two
  signals, in that order, both scoped to what **this session already saved**
  (`_find_session_saved_automations`): the `refine_automation_id` the model
  returned, then the proposal's alias, compared case- and
  whitespace-insensitively the way the user reads it. Explicit refinement — the
  user loaded an automation to edit — still wins outright.
- **The alias and YAML come off disk, never off the chat message.** The message
  records what was PROPOSED. `_acceptAutomationWithEdits` applies the card's
  YAML editor on the way to the write, and `set_automation_status` then
  persists only the status and the id — so the message keeps the pre-edit text,
  and the Automations tab, a version restore, and HA's own editor do not touch
  it either. `_find_session_saved_automation_ids` takes the ids from the
  session — from the `saved_automations` index **and** the retained messages,
  unioned, because `append_message` prunes a long session to its first message
  plus the latest 99 and an id whose message is gone is a follow-up that
  creates a duplicate, while the index only covers saves made since it shipped
  (same index-plus-scan pair as `_find_active_scenes`, for the same reason).
  `async_yaml_automation_snapshots` then reads each one's current alias and
  YAML from `automations.yaml` in a single pass, dropping ids the file no longer
  carries. Sourcing the message instead had a follow-up silently revert the
  user's own accept-time edit, and an accept-time RENAME left the alias
  mismatched — sending the follow-up back to creating the duplicate this whole
  section exists to prevent. It also means `_resolve_proposal_write_target`
  needs no separate existence check.
- **The model can only claim an id because it was given one.** Every automation
  the session saved rides in the user message as reference data — id, alias,
  current YAML — under `AUTOMATIONS SAVED IN THIS SESSION`
  (`automation_context`). `_automation_reference_context` bounds it — 8K chars,
  newest first, since that is the automation a follow-up most likely means —
  and returns **two** lists from that one walk: what the model is shown, and
  which automations an inferred edit may therefore target. They are one
  decision. An automation whose YAML does not fit is still NAMED, with an empty
  YAML string that the prompt builder renders as `_AUTOMATION_TOO_LARGE_NOTE`,
  so the model can say it is too large to edit rather than guess at it — but it
  is NOT editable, because a proposal for an automation nobody showed the model
  is a rule composed from nothing, and writing that over the original discards
  everything the user did not mention. On MCP the same reasoning refuses an
  EXPLICIT refinement too: that surface has no confirmation card between the
  revision and the write — the caller is told to pass the id straight to
  `selora_create_automation` — so the refusal points it at reading the YAML and
  editing it itself. The panel's Refine keeps its diff, which is what lets it
  stay. The YAML is withheld rather than
  truncated for the same reason. The budget lives there rather than in
  `_build_chat_messages`, which only renders, so the inclusion decision and its
  consequence cannot drift apart. **`LLMClient.shows_automation_reference` is
  the same question at provider scope**: the low-context path builds a minimal
  prompt with no room for YAML, so nothing there is editable either. An
  explicit refinement is unaffected — the user or the MCP caller named the
  target, and the panel diffs it before the write.
  Without the context at all the model rebuilds
  the automation from whatever tool results it can still see: same rule, but
  re-derived fields, entity_ids where the description had names, and an alias
  it may not reproduce — which is what made the duplicate unavoidable rather
  than merely likely. `_AUTOMATION_REFINE_RULES` (both prompt builders) asks it
  to start from that YAML, change only what was asked, keep the alias, and name
  the id it is editing.
- **This is reference context, NOT `refining_context`.** The refinement
  sections tell the model it is modifying one specific automation and must not
  build anything else, and a present `refining` also suppresses command intents
  for the whole turn (`_REFINEMENT_SUPPRESSED_BY_LANG`). Reusing it for every
  saved automation would hijack "now make one for the porch" and swallow "turn
  the kitchen light on" in the same session. Same split as scenes:
  `scene_context` is reference, `refining_scene_context` is the directive.
- **The claim is checked, never trusted.** `_pop_refine_automation_id` takes it
  out of the payload — it is conversation metadata, not an automation field, and
  the payload is re-validated, echoed back on a correction round, and walked by
  the risk assessor — bounds its shape, and the resolver accepts it only if it
  names an automation this session saved. The model is quoting untrusted text
  back at us, so an id from anywhere else must not select a write target. A
  correction round never sees the reference context, so
  `_retry_invalid_automation` carries the original claim across its rounds —
  which means **every rejection path has to put the claim on the envelope**,
  not just the success path. It is popped from the payload before validation
  runs, so a rejection dict assembled without it leaves the retry loop with
  nothing to carry, and a corrected proposal that also renamed the automation
  then resolves to no target and is accepted as a second one. The JSON-mode
  path mutates `data` in place and keeps it; the streamed path builds a fresh
  dict and has to copy it over.
- **Session-scoped on purpose.** An alias collision with an automation from
  another conversation stays a create: silently overwriting one is worse than
  the duplicate, and within the session the user was just shown that card,
  which is what makes either signal evidence rather than coincidence. A
  differently-named proposal with no claim is always a create — "now also make
  one for the porch" is a new automation.
- **A target absent from `automations.yaml` is not a target.** It never enters
  the snapshot list, so neither signal can select it. `async_update_automation`
  matches on the id and fails outright when it is gone, so without that a
  re-proposal of an automation deleted between turns becomes a failed save
  instead of a fresh one. Read off the file, not the registry — an entry whose
  entity was never materialised is still there to update.
- **A `history: []` override clears it**, alongside `refining` / `refining_scene`
  / `scenes`. A caller asking for a clean slate must not have its proposal land
  on an automation the supplied history never mentions.
- **MCP asks the same question with different handles.** `selora_chat` takes
  `refine_automation_id` — an id from `selora_list_automations` or an earlier
  create, since a proposal this tool returns has none yet — resolves it to the
  automation's on-disk YAML (`_refining_context_for`), and passes that as
  `refining_context`: an external agent naming one target IS the directive
  case, unlike a panel follow-up. An unresolvable id is **refused**, because
  ignoring it turns "change the time to 7am" into a second automation beside
  the one the caller meant. The response reports `refine_automation_id`, and
  `selora_create_automation` takes it as `automation_id` to replace rather than
  append — restricted to Selora-managed entries, the split
  `_tool_accept_automation` already makes: `async_update_automation` re-validates
  through the proposal validator, which hand-written YAML need not satisfy.
  Note `architect_chat`'s first two arguments are positional (`user_message`,
  `entities`) and `existing_automations` holds records, not alias strings —
  `tests/test_mcp_chat_tool.py` pins the call with `autospec` so a renamed
  parameter fails a test instead of every MCP chat turn.
- **A description is prose, so entity_ids are rewritten as friendly names.**
  It heads the card in chat and is the subtitle of the row in Settings →
  Automations, and a model that reached the device through a tool call
  routinely writes back the id it was handed —
  "Turns switch.basement_pool_room_grillplats_plug_aqua_rite on at 07:00".
  `_humanize_description_entity_ids` runs at the two points in `parsers.py`
  where a validated proposal is finalized, and replaces only ids the state
  machine resolves, so a sentence boundary, "at 7 a.m.", and an id belonging to
  no entity are left alone (`entity_id_in_description` repair counter).
  **Template spans are skipped wholesale**, not left to the state lookup: the
  id in `{{ states('sensor.temperature') }}` resolves like any other, so the
  lookup cannot tell it from prose, and rewriting it produces template text
  that no longer renders. `_PROSE_ENTITY_ID_RE` matches `{{ … }}` / `{% … %}` /
  `{# … #}` as its own alternative and passes it through verbatim; each opener
  also matches to end-of-string, so an unterminated template shields what
  follows rather than exposing it.

## Groups

`group_manager.py` backs four chat tools — `list_groups`, `create_group`, `update_group`,
`delete_group` — so the LLM can offer a group when an automation would otherwise repeat the
same long entity list. The automation then targets one stable entity_id and the homeowner
edits membership in Settings → Devices & Services → Helpers without the automation changing.

- **Helper config entries, not YAML.** We drive HA's own `group` config flow
  (`flow.async_init("group")` → **menu** step `{"next_step_id": <group_type>}` → form). Note the
  first step is a `SchemaFlowMenuStep`, so the form-only loop in `recipes/ws.py` does *not*
  work here. All state lands in `entry.options` (`group_type`/`name`/`entities` + per-type
  extras); `entry.data` stays empty. This is deliberately **not** the legacy `group:` YAML route
  that recipes v3 uses (`recipes/renderer.py`) — that one is scoped to a self-contained package
  file, while chat-created groups must be first-class, UI-editable helpers.
- **Per-domain by construction.** A helper group holds one domain, so `infer_group_type()`
  refuses mixed light+switch membership with guidance instead of dropping members.
  `sensor`/`number`/`input_number` are the one exception — they combine into a numeric `sensor`
  group, which additionally *requires* a `type` statistic (defaults to `mean`).
- **Inapplicable per-type options are policed in code**, since `_build_create_payload` can only
  forward an option to the schemas that accept it (the others are `vol.PREVENT_EXTRA`) and the tool
  schema can't express "only when the members are numeric". Which way depends on whether the option
  can carry user intent for the target type:
  - `requires_all_members: true` is **rejected** off `binary_sensor`/`light`/`switch`. Every
    state-combining type makes it meaningful ("closed only when every cover is"), so a caller
    asking for it on a cover group wants something real that we cannot deliver — reporting
    `status: created`/`updated` would claim a setting that was ignored. **`false` is dropped**,
    not rejected: it is already the behaviour of a type with no all-members mode, so it discards
    no intent, and the tool schema declares `default: false` — refusing it makes every
    cover/lock/fan/valve group a dead end for any client that materializes schema defaults. The
    drop happens *before* the no-op guard on the update path, or a `false`-only update would
    reload the entry, write nothing, and still report `status: updated`.
  - `statistic` is **dropped** off `sensor` (with a debug log). Only a sensor group holds a number,
    so "mean of two lights" is not a request a user can make and there is no intent to discard.
    Models volunteer it from the enum, and refusing turned "group my two lights" into a dead end.
    Its value is validated *after* the drop, so a bogus statistic on a light group is ignored too.
  - **A new per-type option needs one of these two treatments** — pick by asking whether a user
    could have meant it for the types that cannot store it.
- **`entities` and `add_entities`/`remove_entities` are mutually exclusive** — replacement vs delta
  are different intents, and an LLM call can carry both; applying replacement and dropping the
  deltas would report success having ignored part of the request.
- **An empty optional argument is treated as absent** (`_is_empty_delta`, and the `new_name`
  normalization). Every gate tests `is not None`, and models routinely emit `[]` / `""` for
  optional params they are not using — so `add_entities: []` alongside a rename otherwise reads
  as a present argument and refuses the rename with an error about entities, and alongside
  `entities` it trips the mutual-exclusivity check. Same question as the per-type options: "add
  nothing" and "rename to nothing" are not requests a user can make, so there is no intent to
  discard. `entities: []` is the exception and keeps its refusal — *emptying* a group is
  something a user can ask for, and the error points at delete instead.
- **A stored member may be a registry id, not an entity_id.** HA's entity selector validates with
  `cv.entity_id_or_uuid` and keeps whichever form it was given, so a UI-created group's
  `options["entities"]` can hold uuids. Three rules, all served by `_resolve_members()`:
  - **Compare resolved.** Domain inference, the self-reference guard, removal matching, addition
    dedup, and the added/removed diff all run on entity_ids. Raw comparison makes a rename fail
    (`infer_group_type()` reads the uuid as a domain), a removal-by-entity_id miss, an add-existing
    duplicate the member, and a same-list replacement read as "all removed, all added" — which
    then unhides members the group still holds.
  - **Store the form already on record.** A registry id survives an entity_id rename, so a
    *retained* member keeps its stored representation even when the caller named it by entity_id
    (`stored_by_entity_id`); only genuinely new members are stored as given. Normalizing would
    quietly weaken a group the user built in the UI.
  - **Report resolved.** `describe_group`/`list_groups` and every update result return entity_ids —
    the caller is an LLM that quotes them back and feeds them to entity-based tools.

  A stored id whose entity was since **deleted** stays unresolved, and `async_update_group`
  **refuses any update while one is present**. It is not merely cosmetic: the group platform
  re-runs the saved list through `er.async_validate_entity_ids` on setup, which raises on an id
  resolving to nothing, so the post-save reload leaves the group entity `unavailable` — while the
  config entry still reports `LOADED`, meaning neither `async_reload()`'s result nor `entry.state`
  detects it. A plain rename would brick the group and return `status: updated`. The error names
  the exact stale string, because with no entity_id left that is the caller's only handle on it;
  `remove_entities` with that string, or a replacement list omitting it, clears the block. Reads
  (`describe_group`) still surface the raw id — only *writes* are gated.
- **Unhiding a member is conditional on the other groups.** `hide_members` hides the *entity*, not
  the membership, and an entity can sit in several hidden groups. Both paths that unhide have to
  check `_members_free_to_unhide()` first, or a surviving hidden group is left with a visible
  member and nothing in its own config to explain why:
  - *Update* — a removed member is released only if no other `hide_members` group lists it.
  - *Delete* — `group.async_remove_entry` unhides unconditionally, so
    `_hides_to_restore_after_delete()` captures the still-claimed members **before** removal (the
    options are gone after) and re-applies the hide, restricted to members HA will actually clear
    (`hidden_by == INTEGRATION`).
- **A `hidden_by == USER` entity is never touched, in either direction.** `_apply_member_visibility`
  skips it before doing anything. Unhiding it would undo the user's choice outright; *re-hiding* it
  looks like a no-op — it stays hidden — but it transfers ownership to the integration, so removing
  that member later, or deleting the group, releases a hide the user set for their own reasons.
  Creation can't use that guard — it runs HA's real flow, and
  `async_config_flow_finished` → `_async_hide_members` writes `INTEGRATION` unconditionally with no
  opt-out — so `async_create_group` captures `_user_hidden_members()` before the flow and
  `_restore_user_hides()` after it.
- **`describe_group` caps `members` at `_MAX_LISTED_MEMBERS`** (`member_count` stays exact,
  `members_omitted` reports the difference). Not cosmetic: `ToolExecutor._find_longest_list` only
  looks at top-level lists and lists inside top-level dicts — it never descends into a list *of*
  dicts — so one oversized group made it pop the entire `groups[0]` record and the chat caller got
  `count: 1` with no name, entity_id, or members. Any tool returning a list of records with inner
  lists has the same exposure.
- **A numeric group refuses a member that reports text** (`_non_numeric_members`, checked on create
  *and* on update). `ignore_non_numeric` defaults to False, which does **not** mean "refuse":
  `SensorGroup` drops the unparseable member from the calculation and logs a warning, so the group
  is created, reports success, and lists a member contributing nothing (all-text members →
  `unknown`). `unknown`/`unavailable` states are allowed through — a numeric sensor reads both
  while its device is offline, and refusing then would tie group creation to a flat battery.
- **A group may not contain its own entity.** HA's options flow makes that unrepresentable via
  `entity_selector_without_own_entities`; we bypass that flow, so `async_update_group` checks
  `own_entity_ids()` before saving — a self-referential group tracks its own state and can loop.
  Nesting a *different* group stays legal.
- **No store.** Groups are read live from config entries — nothing to reconcile or drift.
  `unmanaged_yaml_groups()` surfaces legacy `group.*` entities read-only so a request to edit one
  gets an explanation rather than a duplicate. `resolve_group()` checks the YAML case **before**
  the "no group helpers yet" shortcut and on the by-name path as well as by-entity_id — a home
  whose only groups are YAML-defined must still get that explanation, or the model reports a group
  the user can plainly see as missing and offers to create a duplicate.
- **Updates must reload.** `group`'s `async_setup_entry` registers no update listener and we
  bypass the options flow, so `async_update_group` calls `async_reload` itself — without it the
  live entity keeps tracking the old member list.
- **Create/update execute directly; delete goes through the confirmation card** (`kind: "group"`,
  `target_id` = the immutable config `entry_id`, so unlike the id-less scene/automation paths
  there is no fingerprint to re-verify). The card label carries the blast radius from
  `group_dependents()` because the tool-loop short-circuit discards prose the model writes.
  That covers four referrers, and only the first two have an HA helper: automations and scripts
  (`automations_with_entity` / `scripts_with_entity`), **scenes** (read off the scene state's
  `entity_id` attribute — `scene` ships no `scenes_with_entity`), and **parent groups**. Nesting is
  supported, so a parent is ordinary — and it does not break when the child is deleted, it silently
  gets smaller, which is exactly why the card has to say so. Parents need **two** disjoint lookups,
  unioned in `group_dependents`: `parent_groups()` walks helper config entries and never sees a
  legacy YAML group, while HA's `groups_with_entity` (`_yaml_parent_groups()`) walks only the legacy
  component's entities and never sees a helper. A group can sit in both kinds at once, and recipes
  write legacy YAML groups — so a helper nested in one is ordinary, not a corner case.
- Adding a tool here means touching `group_manager.py`, `mcp_server.py` (`_tool_*` + `MCPTool`
  schema + name constant + handler map + `_ADMIN_TOOLS`/`_READ_ONLY_TOOLS`), `tool_registry.py`
  (`ToolDef` + `CHAT_TOOLS` + `COMMAND_TOOL_NAMES`), and `tool_executor.py`. `COMMAND_TOOL_NAMES`
  is not optional: `_classify_chat_intent` falls through to `"command"` for group phrasings, which
  trims the schema to that set.

## Blueprints

`blueprint_manager.py` backs `list_blueprints` and `get_blueprint`. A blueprint
is a parameterised automation or script template and one of the commonest ways a
home gets its automations, and none of it was visible here.

- **Reachable in-process**, unlike helpers and dashboard entries:
  `hass.data["blueprint"]` is a `dict[domain, DomainBlueprints]` published by the
  automation and script components — the same object the websocket API serves.
- **The reads are useless without the write.** A blueprint automation's config is
  `use_blueprint: {path, input}` and carries NEITHER triggers nor actions, so
  `validate_automation_payload` rejected every one of them with "must include at
  least one trigger". Listing blueprints you cannot build anything from is not a
  feature; the validator now takes a `use_blueprint` branch that returns early.
  **Every write path needs its own branch**, and there are three.
  `validate_automation_payload` accepts the shape; `prepare_write_payload` must
  strip **both** key spellings (HA merges a surviving `actions` OVER the
  blueprint's substituted config — an empty list invalidates the automation, a
  populated one silently replaces what the blueprint does, and a payload
  carrying both shapes is ordinary after converting one in the YAML editor); and
  `async_create_automation` must copy `use_blueprint` into the entry it writes
  instead of the default empty `triggers`/`actions`. Fixing only the first two
  moves the failure rather than removing it — and the last one fails SILENTLY:
  HA logs the invalid item at reload while the function returns success, so a
  test asserting `result["success"]` proves nothing. **Assert the written YAML.**
  What the blueprint branch does NOT get to skip is the outer fields. `mode`,
  `initial_state` and the YAML round-trip check are as applicable to a blueprint
  instance as to any other automation, and returning early past them let
  `mode: "garbage"` reach the file with the same silent failure. `_finalize_payload`
  is the one implementation both branches end in; only the triggers-and-actions
  work is genuinely branch-specific. And `prepare_write_payload` must COPY the
  normalized outer fields onto the payload it mutates — normalizing a field the
  writer then takes verbatim from the original is theatre. That applies to the
  ordinary path too, which had the same hole: `mode: " Restart "` validated as
  `restart` and was written unchanged. `id` and `initial_state` stay out of it —
  `apply_managed_fields` owns those.
  Risk assessment records `blueprint_unassessed` as a **scrutiny tag, not a
  flag**. The actions live in the blueprint file so there is nothing in the
  payload to inspect, but any flag forces `elevated`, which would land every
  blueprint automation disabled — and a blueprint is a file the user installed
  themselves. Reporting a bare `normal` would instead claim we looked.
  It checks the SHAPE only (`path` present, `input` a mapping) — whether the
  inputs satisfy the blueprint is the blueprint's own schema question, which HA
  answers at reload using the author's selectors, and restating that here would
  be a second copy that goes stale when the blueprint changes.
- **`get_blueprint` returns selectors and required-ness.** The selector is what
  makes the difference between a working automation and one HA rejects at
  reload, and "required" is the absence of a `default` — neither is derivable
  from the listing, so composing an automation without this call is guessing.
- **A blueprint that fails to parse is reported, not dropped.**
  `async_get_blueprints` returns the exception in place of the blueprint rather
  than raising; skipping it silently leaves the user wondering where their file
  went.
- **Only an AUTOMATION blueprint can back an automation.** `list_blueprints`
  returns every domain by default, and `automations.yaml`'s loader searches only
  the automation store — so a script or template blueprint's path writes an
  entry HA rejects at reload while the write succeeds. The tool descriptions say
  automation-domain only; `_blueprint_path_error` is what makes it true, because
  the failure is invisible to the caller either way. It lives inside
  `prepare_write_payload` — which is **async for that reason** — so a write path
  added later cannot skip it: converting an existing automation to a blueprint
  through the YAML editor reaches the update path, not the create path. And
  membership in the store is not loadability: a malformed or wrong-domain file
  is reported as the EXCEPTION in place of the blueprint, so the value is
  checked, not just the key. It is skipped when
  blueprints are not set up: a missing store is not evidence the path is wrong,
  and blocking every blueprint automation on it would be worse than the case it
  guards.
- **The reads are admin-gated, like HA's own.** `blueprint/list` carries
  `@websocket_api.require_admin`, so a non-admin chat user or read-only MCP
  credential must not reach these either — a blueprint carries its author's
  source URL and input defaults, config detail HA does not show a non-admin.
  Same reasoning as `get_logs` / `get_automation_traces`: read-only is not the
  same question as unprivileged, and matching HA's boundary is what keeps this
  tool surface from being a way around it.
- **Import is deliberately absent.** It means fetching YAML from a URL and
  writing it to the config directory, and a URL an LLM chose — possibly off a
  page it was asked to summarise — is a different risk class from the registry
  edits alongside it. It belongs behind a confirmation card naming the source.

## Registry, script, label, and diagnostic tools

`registry_manager.py`, `script_manager.py`, `label_manager.py`, and
`diagnostics_tools.py` back the config-management half of the chat tool surface —
the tools that reshape the home rather than operate it. They exist because the
model could see every entity's area, name, and alias in the home snapshot but had
no way to change one, so it fell back to reciting the Settings click-path.

- **Floors are full CRUD now, and deleting one is a confirmation card.** They
  used to exist only as a side effect: `_ensure_floor` creates one when an area
  names a storey that does not exist, so a home could accumulate floors with no
  way to see or remove them. Deleting a floor does NOT delete its areas — HA's
  area registry listens for the floor-removed event and clears each
  `floor_id` — but nothing announces it, so the card NAMES the areas rather than
  counting them: "and 4 areas" does not tell the user whether the one they care
  about is among them. `floor_id` is derived from the name exactly like
  `area_id`, so it is reusable once the floor is gone and the descriptor carries
  `created_at` for the confirm handler to re-check.
- **`list_floors` orders by `level`, and an unset level sorts LAST.** Level is
  the only field carrying the storeys' real relationship — "what is upstairs"
  cannot be answered from name order — and unset is not the ground floor.
  `_opt_level` exists for the same reason: the blank-is-absent rule the other
  adapters use would read `level: 0` as "not set" and leave the ground floor
  unordered.
- **An entity's area is an override of its device's area.** `async_assign_area`
  therefore has two correct outcomes: when the entity's device is *already* in the
  target area it **clears** `area_id` so the entity inherits, and only otherwise
  does it write the override. Both look identical today; the stored override
  outlives the coincidence, so pinning would strand the entity in the old room the
  next time the device moves, with nothing in the UI to explain why one of a
  device's entities did not travel with it. The result names the two groups
  separately (`entities_assigned` vs `entities_now_inheriting`) because a caller
  reading the registry back would otherwise see a blank `area_id` and read the
  assignment as failed.
- **`AreaEntry` exposes `.id`, not `.area_id`.** `FloorEntry` keeps `.floor_id`
  and entity/device entries keep `.area_id`, so the three read alike and only one
  is wrong. It fails at runtime as an `AttributeError` inside a tool call, which
  surfaces to the user as "Tool execution failed".
- **Renaming an entity_id rewrites nobody's references.** HA does not touch
  automations, scripts, scenes, or dashboards, so `async_update_entity` refuses
  `new_entity_id` while anything references the old id and names the referrers
  (reusing `group_dependents`, which is entity-generic despite the name).
  `new_name` — the friendly name, which is what "rename this to X" means — is
  always allowed.
- **Deleting an area unassigns rather than deletes**, and does so silently: every
  automation targeting `area_id: living_room` keeps loading, keeps validating, and
  matches nothing. Hence the confirmation card, whose label carries the counts.
- **`scripts.yaml` is a mapping keyed by object_id**, not a list of dicts carrying
  their own `id` like `automations.yaml`. `script_manager` does not reuse
  `automation_utils`' readers for that reason — the same code shape would write a
  list HA then ignores. It does reuse `_quote_yaml_booleans` and `_to_plain_types`.
- **`set_script` replaces wholesale**, so `get_script` must be called first when
  editing. HA's own `async_validate_config_item` runs **before** the write, or a
  bad sequence lands in the file, fails at reload, and leaves the user to fix it by
  hand. A *reload* failure after a successful write is reported as
  `reload_error` alongside the write rather than raised: raising would tell the
  user nothing happened on a change that in fact landed and will appear at the
  next restart.
- **Categories are labels with a scope, and that changes three things.** A
  category lives under a scope string (HA keeps a separate list per Automations
  / Scripts / Scenes / Helpers page) and an entity holds **at most one per
  scope** — `RegistryEntry.categories` is `{scope: category_id}`, not a set.
  So: a name is unique only WITHIN a scope, which is why `resolve_category`
  needs no ambiguity handling for the EXACT name — but the registry's uniqueness
  check is `name.casefold()` and nothing more, so `"Outdoor Lights"` and
  `"Outdoor  Lights"` are two categories a user can genuinely have.
  `resolve_category` matches HA's comparison first, then falls back to
  collapsed-whitespace matching only when that is unambiguous: being forgiving
  about spacing is what a caller typing from memory needs, but picking the first
  of several is the silent mis-targeting the exact match exists to prevent.
  **A duplicate CHECK must not use a forgiving resolver** — the same trap
  caught `create_floor`, whose resolver matches aliases, so a floor named after
  another's alias was refused although HA enforces uniqueness on the name alone.
  Ask the registry's own name lookup.
  **`create_category` must NOT use that resolver** — its loose match would call
  a name HA allows a duplicate and skip a creation HA would accept, so it
  compares casefolded names directly.
  **The scope check applies only when ASSIGNING.** Clearing removes a mapping
  that already exists, and an entity is often out of scope precisely because
  something wrote a stale one — or because its helper integration's metadata is
  momentarily unreadable. Refusing then makes the bad state unfixable through
  the tool that caused it. The
  scope is still required rather than searched across; `assign_category` writes one scope's key and leaves the rest alone,
  for the same reason label assignment is a delta; and the delete card's
  `target_id` is `"<scope>#<category_id>"`, because the same name under two
  scopes is two categories and a bare id would not say which the card meant.
  **An entity the scope's page never lists cannot be filed there**
  (`_SCOPE_DOMAINS`): the mapping would be written, the count would go up, and
  the user would see it nowhere. Only the scopes whose page contents we know are
  policed — a scope outside that map is one HA may have added or the user
  invented, and refusing every entity under it is worse than the case it guards.
  **`helper` cannot be answered from the entity domain.** The storage-collection
  helpers own theirs (`input_boolean.*`), but a template, utility-meter,
  derivative or threshold helper is an ordinary `sensor.*` / `binary_sensor.*`
  that the Helpers page still lists, so a domain allowlist rejects every one of
  them. Membership comes from the config entry's integration declaring
  `integration_type: helper` — the same question `helper_overview` asks.
  The scope is free-form server-side — HA's own tests create categories under
  `"bullshizzle"` — and the real strings are frontend-owned, so core cannot be
  read to confirm them: the Helpers page uses **`helper`, singular**, unlike the
  other three. `UI_SCOPES` rides on the tool schema as an enum rather than a
  server-side refusal: a scope no page reads gives a category that exists
  and appears nowhere, but refusing a scope a future HA release adds would be
  worse. Unlike `area_id` and `floor_id`, a `category_id` is a ULID, so it
  cannot be reused by a recreated category and the descriptor needs no
  timestamp fingerprint.
  `category_manager` mirrors `label_manager` down to having **no rename** — a
  category rename is rare and reachable in the UI, and adding one the labels do
  not have would make the two registries diverge for no reason.
- **Label assignment is deltas, never replacement.** Labels are the one registry
  field several unrelated concerns write to at once, so a replacement call from a
  model that only knows about `holiday` would drop the `battery-powered` label
  another flow set. `assign_labels` **creates** an unknown label rather than
  refusing — a label has no contents and nothing can target one that was never
  made, so refusing would cost a round-trip and protect nothing. That is the
  opposite of the area rule, where a typo'd auto-create would silently split a
  home in two.
- **`get_logs` reads `hass.data[DATA_SYSTEM_LOG].records`** — the value is the
  logging *handler*; the deduplicated ring is on `.records`. Traces are keyed
  `automation.<config id>`, not by entity_id, so `_resolve_trace_key` translates
  through the state's `id` attribute; a YAML automation with no `id` is never
  traced and gets that explanation rather than an empty list.
- **Helpers are read-only from chat.** The `input_*`/`counter`/`timer`/`schedule`
  storage collections are locals inside each component's `async_setup` and are
  never published to `hass.data`, so there is no supported in-process way to
  create one (reachable by an authenticated websocket client, same as dashboard
  entries — see that note; the pattern would carry over but each helper domain
  needs its own allowlisted command, schema and validation) — `list_helpers` finds existing helpers to wire automations to, and
  `create_helper` is deliberately absent rather than faked.

### Tool lanes

**Lanes apply to LOW-CONTEXT providers only.** A cloud turn gets the whole
schema. The lanes traded correctness for about 4k tokens — the full chat toolset
is ~9.7k, a lane ~5.7k — and the price was three regexes deciding what the model
was allowed to see. When they were wrong the model did not answer worse, it
reported the capability did not exist: "Create a new office area" was answered
"I can't create areas directly", and dashboard tools sat in neither lane at one
point. A schema that never varies also CACHES, which a per-turn lane prevents,
so the lanes were costing more than the tokens they saved.

`_cloud_intent_hint` asks the PROVIDER: `holds_full_tool_schema`. Neither
locality nor `is_low_context` is the question. `is_low_context` means "≲2K" and
describes Selora AI Local alone; **locality misses OpenRouter**, a cloud gateway
that accepts arbitrary model ids and reports no window — an 8K model there would
be handed a ~9.7K schema and the request REJECTED, not merely answered worse.
Ollama is the same story from the other side: it serves whatever window the
runtime was started with, commonly 4K-8K.

The property defaults to **False**, and the answer is **per MODEL, not per
provider or vendor**. The model field is free-form everywhere: `gpt-4` (8K),
`gpt-4-32k`, `gpt-3.5-turbo` (16K), `gemini-1.0-pro` (exactly 32K, no margin) and
`google/gemma-2-9b-it` are all still selectable and all smaller than the schema
alone, so a blanket per-provider yes — or a vendor prefix on a gateway — makes
those requests FAIL rather than fall back to a lane.
`model_is_known_large` in `providers/base.py` holds one allowlist of families
known ≥128K, matched on the family prefix after any `vendor/` is stripped so a
gateway id and a direct id answer alike. OpenAI, Gemini and OpenRouter all ask
it; Anthropic is the one provider that may answer by catalogue, because every
Claude ever shipped is ≥100K. Anything unrecognised falls back to the base rule,
a REPORTED `context_window` of at least `FULL_SCHEMA_SAFE_WINDOW`.
**It is an allowlist on purpose** — a deny-list has the wrong default, since a
model nobody has classified yet would be assumed roomy. `context_window` of
`None` means UNKNOWN, and `LLMProvider` is explicit that unknown keeps whatever
conservative behaviour was already there — reading it as "fits" gets the
decision backwards.

**Prompt caching covers the system prompt only, and that boundary is a safety
property rather than a tuning choice.** The home's entity states ride in the
CURRENT TURN'S USER MESSAGE — `_build_chat_messages` puts the snapshot there,
never in the system prompt or the tool schemas. So a cache hit replays
instructions and can never replay a reading of the house: a stale snapshot would
have the model answering about a home as it was minutes ago, which is worse than
paying the prefill every time. Any future change that moves state into the
system prompt breaks this and must move the cache breakpoint with it.
Anthropic marks the system block; OpenRouter does the same for `anthropic/*`
models only, since `cache_control` is an Anthropic extension and another
upstream would be handed a key it does not understand. OpenAI caches long
prefixes automatically with no flag.

### Tool lanes (low-context providers)

`TOOL_LANES` in `tool_registry.py` maps an intent hint to the tool subset a turn
is trimmed to; an absent or unknown hint gets the full schema. `LLMClient._cloud_intent_hint`
tests **`config` first**, then `command`.

Order matters: a registry request matches none of the question or automation
patterns, so `_classify_chat_intent` falls through to `command` and would trim the
schema to the device-control lane — hiding exactly the tools the request needs.
This is the same trap documented for the group tools, and it is why `config` is a
second lane rather than more entries in `COMMAND_TOOL_NAMES`: the two sets barely
overlap, and folding them together would hand every "turn off the kitchen light"
turn a dozen registry-editing tools while the command lane exists precisely to
keep that schema small. Only the entity-resolution tools appear in both.

- `_is_config_request` in `llm_client/intent.py` is **separate from
  `_classify_chat_intent`** on purpose: that function's four return values map to
  trained LoRA specialists, and a fifth would route traffic at one that does not
  exist. A false negative there is cheap (full schema, which contains everything);
  a false positive is expensive (strips `execute_command` from a turn meaning to
  switch something on), so every pattern requires vocabulary a device command has
  no reason to use. `_PLACEMENT_VERB` plus a **live area name** closes the gap the
  regexes cannot — "move the lamp to the Study" carries no area noun.
- Script *creation* is deliberately NOT claimed by the config lane. "Create a
  script that turns the lights off at 11pm" is automation-shaped and needs the
  device-trigger and template tools; only the management verbs are claimed.
- `delete_area` / `delete_script` / `delete_label` are in **both** lanes, for the
  reason `delete_automation` already is: "get rid of the Movie Night script" falls
  through to `command`.
- **Every new tool here is `large_context_only=True`.** The low-context path sets
  `tool_executor = None` outright so Selora AI Local never receives a schema, but
  `_get_tools_for_provider` is also reachable from the Assist conversation path,
  and a 1.7B model handed a registry-editing schema will call it.

### Adding a delete tool

`_DELETE_TOOLS` and `_DELETE_KINDS` in `llm_client/command_policy.py` are both
allowlists, and a new delete tool must be added to **both** plus a branch in
`_resolve_approval` (`__init__.py`). Missing either fails silently in the worst
shape available: the tool returns `requires_approval`, the tool loop
short-circuits on it and discards the model's prose, and the synthesizer then
drops the descriptor — the user gets an empty reply and no card.

The MCP definitions for all of these are **derived** from the chat `ToolDef`s
(`_DERIVED_MCP_TOOLS` / `_mcp_tool_from_chat_tool` in `mcp_server.py`) rather than
restated. A hand-written second copy drifts on the next parameter added, and the
failure is quiet: the MCP client rejects an argument chat accepts, on a tool that
looks identical in both listings.

## Resumption — continuing after a confirmation

A turn that proposes something ENDS there. The tool loop short-circuits on
`requires_approval`, and the handler that runs after the button appends one line
and stops. So "create a dashboard for the Office **with my Office devices**" and
"create a scene **and add it to the dashboard**" each produced the first half and
dropped the second, silently, while the reply read as complete. Resumption is the
general fix; before it, each tool would have needed its own way to carry work
across the gap (`create_dashboard` briefly grew a `views` parameter for exactly
that, since removed).

- **The confirmation is the re-entry point, and it has to be.** The thing the
  second half needs — the dashboard, the scene entity — does not exist until the
  user taps. The model is RIGHT to split the work; what it lacked was anywhere to
  put the second half.
- **It re-enters `selora_ai/chat_stream` with `resume_proposal_id`.** The same
  streaming path, so the continuation streams and shows its tool steps like any
  turn, and there is one implementation to keep right rather than two.
- **The client sends an id and nothing else.** The directive, the approval status
  and the depth are read from the STORED proposal. A panel can type any `message`
  it likes anyway, but it must not be able to resume a card nobody approved or
  slip past the cap.
- **Two doors, one resolver.** `_resume_request` answers for a `command_approval`
  proposal_id (client actions, service approvals, deletes) AND for a `scene_id`,
  because a scene is proposed as a block and accepted from its own card — the
  handle it comes back with is the scene id. Refused on a denial, on an unsaved
  scene, and on an unknown id.
- **A declaration is preferred, not required.** `remaining_intent` on a carded
  tool result (lifted once in `ToolExecutor.execute`, never per handler) or beside
  a scene block is precise, so the continuation knows exactly what to do. But the
  model announced the follow-up in PROSE three times running — "once you accept
  the scene below, I can add its dashboard tile" — and left the field unset, and
  two rounds of firmer wording did not change it. So when it is absent the user's
  own last message is REPLAYED with the note that the proposal now exists, and
  the model decides what is left of it. Their words, not a guess at the remainder.
  The price is a wasted round when the proposal was the whole request, which is
  why that directive says plainly that a short confirmation is the right answer
  then — a model told only to "continue" invents work to justify the round.
- **The replayed request is RECORDED on the proposal, not located later.**
  `origin_request` is written beside it at append time, because the walk back
  through the session that used to find it is right only while the session is
  intact: pruning keeps the very first message pinned ahead of the latest 99, so
  a proposal that survives at the head of that tail has its own request gone and
  the scan lands on the pinned one — an unrelated request from the start of the
  conversation, replayed as though the user had just made it. Same shape as
  `refining_automation_id`, and for the same reason: the value is knowable only
  in the turn that proposed, and the tap comes much later. The scan stays as the
  fallback for proposals written before the field, since a card can outlive a
  deploy and the failure it guards needs a 100-message session to reach.
- **The cap is once**, by two mechanisms that come to the same thing: a card
  proposed DURING a resumed turn is stamped `resume_depth: 1` and refused
  thereafter, and a resumed turn persists no `remaining_intent`, so there is
  nothing to resume from. Without a cap a model that keeps proposing one more
  step runs unbounded on the user's account.
- **The directive is never persisted.** It is server-written, and storing it
  would put words in the user's mouth in their own transcript and be read back on
  every later turn as something they said. `_persist_user_turn` skips it.
- **`remaining_intent` must not reach MCP.** It is the resumption trigger and MCP
  has neither card nor panel, so a client passing it is answered by nothing —
  worse than an absent parameter, because an agent uses it and then waits.
  `_mcp_tool_from_chat_tool` drops `_PANEL_ONLY_PARAMS` from `properties` AND
  `required`; a schema naming a required property it does not define is invalid
  and a strict client rejects the whole tool. This shipped once, on five derived
  tools, because the deriver copies the chat schema verbatim.

## Dashboards

`dashboard_manager.py` backs the chat tools that read and edit Lovelace content —
`get_dashboard`, `get_dashboard_card`, `add_dashboard_view`,
`update_dashboard_view`, `remove_dashboard_view`, `update_dashboard_card`,
`remove_dashboard_card`, `delete_dashboard` — alongside the older
`list_dashboards` / `insert_dashboard_card`. It is separate from `recipes/dashboard.py`, which is the
recipe install stage; this module reuses its `_view_card_lists` but nothing else.

- **A dashboard ENTRY cannot be created IN-PROCESS** — which is not the same as
  impossible, and the distinction matters because the categorical version sent
  us round in circles. `DashboardsCollection` owns adding and
  deleting dashboards and is a local inside `lovelace.async_setup`, published only
  to the admin-only `lovelace/dashboards/*` websocket commands — never to
  `hass.data`. Current core exposes exactly one lovelace service
  (`reload_resources`). The supported API exists; it is reachable only by an
  authenticated websocket CLIENT, which an in-process integration is not — which
  is exactly how ha-mcp does it. `create_dashboard` is therefore absent here.
  **The panel does it** (`create_dashboard`), being already an authenticated
  websocket client (`hass.callWS`). Four things constrain the design:
  - It serves **interactive panel sessions only**, and the CALLER declares that,
    not the model. `panel_only` on the `ToolDef` withholds the schema unless
    `_get_tools_for_provider` is passed `panel_available=True`, which the three
    panel entry points do: the chat handler, its correction round, and the
    streaming path. It defaults to False so a caller that says nothing gets
    less. **`for_assist` is not the same question** — Assist has no panel, but
    neither does an MCP `selora_chat` turn, and that one passes
    `for_assist=False`, so gating on it offered the tool on a surface that
    cannot execute the proposal.
    Describing the restriction in the tool's own text instead — "if this is not
    a panel chat, tell the user to add one in Settings" — asked the model to
    judge which surface it was talking through, which nothing in its context
    reveals. It hedged and refused: *"this chat can only create dashboards from
    the Selora panel. Please open this conversation in the Selora panel"*, to a
    user sitting in the panel. **A fact the model cannot observe must not be a
    condition it applies**; the tool's PRESENCE has to carry it, and the
    description now states availability flatly. The same doubt is what had it
    describing the dashboard it *would* make instead of calling anything.
  - The panel must never be handed a websocket payload the MODEL authored. The
    server sends a closed, validated intent (`{kind: "create_dashboard", title,
    url_path, …}`) and the panel constructs the fixed
    `lovelace/dashboards/create` call itself. That boundary is the whole
    security model: anything looser makes the model able to issue arbitrary
    admin commands through the user's session.
  - It goes behind a confirmation button, but **not because creation is
    destructive** — the rule elsewhere here is that create/update execute
    directly and only deletion confirms. It is because this is a *deferred
    client-side privileged operation*: the panel must report the real websocket
    result back, or Selora claims success for something that has not happened
    yet, which is the failure this section already documents twice.
  - The result handler must resolve the proposal to its MESSAGE INDEX
    (`_find_pending_approval`) before calling `set_approval_status`, which
    addresses by position — a proposal_id compared against an int silently
    matches nothing. And `append_message` takes `role`/`content` as separate
    arguments, not a dict. Both are the shape that looks right and raises at
    runtime, and only a test that drives the handler catches them.
  - **One `command_approval` fits per message**, so a client action proposed
    beside a service call, delete or destructive action LOSES — it is a
    proposal, nothing has happened, and it can be re-requested — and is then
    NAMED in the reply, folded into the existing "I have not touched …" notice.
    The naming has to happen after the winning card is built: both builders
    replace `response` with their own wording.
    **The MODEL'S OWN payload counts as competing, not just the tool log.** A
    turn can carry an explicit `command_approval` with calls, or a plain
    `command`/`delayed_command` with calls, and each reaches its slot by a
    different route further down `synthesize_approval_from_tool_log` — so
    weighing only the log returned the client card and discarded the service
    calls outright, which is the one outcome the rule exists to prevent. An
    empty `calls` list does NOT count: deferring to a card that resolves to
    nothing would leave the user unable to act on either.
  - **The card's prose is deterministic, never the model's.** By the time the
    proposal comes back the model has typically already narrated the dashboard
    as created, and carrying that through left a success claim above a button
    that had not been pressed — told to a user who may never press it.
    `_build_client_action_response` overrides `response` with
    `client_action_pending_hint`, exactly as the delete card does, and the
    outcome line (`dashboard_created_line`) is written only once the panel
    reports back. A safe write that ALREADY executed in the same round is
    acknowledged beside the hint, or overriding the prose drops the only
    mention of something that really happened — with its entity tiles stripped,
    since on a dashboard turn a tile reads as a preview of the layout that was
    saved and nothing has been saved at all.
  - The proposal carries the RESOLVED turn language, not `hass.language`.
    `parse_streamed_response` re-runs `resolve_reply_language` for that reason:
    `architect_chat_stream` resolves into a local the streaming caller cannot
    reach, so it passes the panel's UI locale and every deterministic outcome
    built downstream came out English. The resolution is a marker-set lookup —
    pure and cheap enough to redo. The
    panel only knows the UI locale, and a French message on an English-UI
    install must still get a French outcome — only the turn that ran
    `resolve_reply_language()` knows which.
  - The result endpoint resolves ONE kind of card. `_find_pending_approval`
    returns any pending proposal, so it also checks `approval_kind` and that the
    reported kinds answer the stored descriptors — otherwise an authenticated
    admin could mark a deletion approved and append a fabricated outcome for
    work nothing performed. The panel is trusted to report faithfully; it is not
    trusted to say which approval it is reporting on.
  - **The re-entry guard is synchronous, before any await.** A double click
    lands both handlers before Lit rerenders the disabled button, and
    idempotence does NOT cover it: both would finish the dashboard-list request
    before either creates, both would see nothing, and both would create. The
    two guards answer different questions — this one stops a concurrent second
    run, idempotence stops a later one.
  - **The action itself must be idempotent**, because the card can outlive its
    own execution: create succeeds, the report fails, and after a refresh the
    backend still serves the proposal as pending with the button back. A blind
    retry then fails on a url_path that is already taken and records the card as
    denied for a dashboard sitting right there. `create_dashboard` checks the
    dashboard list first and reconciles instead — keeping the resolved state in
    memory only survives until the tab closes.
  - A failed REPORT must not undo a succeeded ACTION. The dashboard exists;
    reloading would swap in the server's still-pending copy, put the button
    back, and invite a second creation. The session id is captured before the
    first await for the same family of reason — switching conversations
    mid-flight otherwise reports the old proposal against the new session.
  - Everything HA's create schema can reject is validated BEFORE the card,
    with HA's own validators (`cv.icon`), not lookalike regexes — the whole
    point of the card is that pressing it works.
  - **The card IS the ask, so the tool must be CALLED, not described.** Told
    only that it proposes and the user then taps Create, the model answered
    with a paragraph about the dashboard it could make and called nothing —
    promising a confirmation card the user was never shown, which reads as the
    request having been carried out. A description telling it to "say what will
    be on it" is an instruction to narrate; that phrasing is gone. The prompt
    already had this rule for REVIEW service calls ("The approval card IS the
    confirmation step") and it now covers every confirmation-carded TOOL —
    dashboards and each of the deletes — in that one block rather than restated
    per description, where the next tool added would miss it.
  - **The slug comes from HA's `slugify`**, not an ASCII character class. This
    ships in 13 locales: a title written in the user's own script ("Кухня",
    "厨房") had no ASCII to keep, reduced to nothing, and came back refused as
    having no usable URL path, while an accented one lost the accented letters
    ("Küche Öl" → `k-che-l`). `slugify` transliterates (`kukhnia`, `chu-fang`,
    `kuche-ol`), as `script_manager` and `scene_utils` already use it to. Its
    **`"unknown"` fallback is caught** rather than passed on: it substitutes
    that literal when nothing survives, so a title of `"!!!"` would quietly
    land at `/unknown` and the next one would collide with it.
  - **A created dashboard has no stored DOCUMENT, and storage cannot tell that
    from a generated Overview.** `lovelace/dashboards/create` makes the entry
    only, and `LovelaceStorage.async_get_info` reports a dashboard whose config
    is None as `mode: auto-gen` — so `_load_or_reason` refused every write with
    the Take control note, `list_dashboards` called it `editable: false`, and
    the user was told to go and do by hand the thing they had just asked for. A
    dashboard nobody can fill is not a dashboard. So a successful report calls
    `async_initialize_created_dashboard`, which saves `{"views": []}` — what
    Take control does, minus the strategy render there is no server-side way to
    perform. The dashboard is empty either way; the difference is that this one
    is editable and the next request can put cards on it. It **never
    overwrites** a document that exists, so a re-report cannot blank a dashboard
    since filled, and a missing entry is a debug line rather than a failed
    report — the create did succeed. Nothing short of an end-to-end test sees
    this: propose and create both reported success throughout.
  - **Reconciling a retry compares every field the create would have set**, not
    just `url_path`. The path was free when the proposal was built, so a match
    at tap time is only sometimes a retry of this action — the user may have
    made an unrelated dashboard there in between, and reconciling to it recorded
    the card approved while the transcript named a title that exists nowhere. A
    retry of our own create agrees on title, icon, `require_admin` and
    `show_in_sidebar` by construction, since they are the values it sent;
    anything else is a real collision and is reported as one, naming what is in
    the way.
  - It would establish a reusable client-executed-command pattern that helper
    creation could also use — but not for free. Each helper domain still needs
    its own allowlisted command, schema, permission handling, result
    reconciliation and tests.
- **The card is a VARIANT of the one confirmation card, not a card of its own**
  (`render-approval-card.js`). `_CONFIRM_VARIANTS` already held `delete` and
  `destructive` under a comment saying they "use the same layout, rows, and
  destructive accent — only the copy differs"; `client_action` is the third
  entry, carrying the accent, head icon, copy, where its rows come from, how one
  renders, and — uniquely — its button, since the panel's own work has no
  server-side resolver while the others get Allow / Deny from `msg.quick_actions`.
  `renderApprovalCard` asks whether the kind HAS a variant rather than naming
  each, so a fourth shape needs no change there. Written as a second renderer it
  had already drifted before shipping: its markup named five classes
  (`approval-card`, `approval-head`, `approval-row`, `approval-buttons`,
  `approve`) that no stylesheet defined, so it rendered as a bare div and a
  browser-default button inside a styled chat. Nothing caught it — the markup
  was valid, the behaviour correct, every test green — so a test now fails on a
  class no stylesheet defines, and that test **globs** the stylesheets rather
  than listing them, having first shipped missing `quick-actions.css.js` and
  reporting a real class as invented.
  - **The button is the quick-action confirm chip**, via `renderConfirmChip`
    exported from `quick-actions.js` — the same component as a risk card's
    Allow / Deny, at `tone: "approve"`. It takes a handler because a client
    action has no quick-action value to send back. A `.btn .btn-primary` from
    the generic family was a second button vocabulary for the identical act, and
    the wrong weight: those tones exist so confirmation buttons "stay visually
    quiet next to the risk card", and a filled button shouted where Allow
    murmurs.
  - **The head icon says what the CARD is; the row icon says what the THING is.**
    Setting both to the dashboard glyph drew it twice, one above the other. The
    delete card gets this right — a warning triangle above per-kind rows — so
    the head is `mdi:gesture-tap` and the row icon comes from a per-kind map, so
    a second client-action kind gets its own rather than inheriting a
    dashboard's.
  - The row label is composed in the FRONTEND from the descriptor's parts, not
    taken from its server-built `label`: that string is English, and rendering
    it would leave one English line in an otherwise translated card.
- **`add_dashboard_view` must neither claim to create a dashboard nor deny that
  anything can.** Both readings produce the same wrong answer. It used to say
  "this is how you 'create a dashboard'", so a request for one was answered by
  appending a page to an unrelated dashboard and reporting success. Replacing
  that with "Home Assistant does not allow one to be created from here — send
  the user to Settings → Dashboards" was no better once `create_dashboard`
  existed three lines away in the same schema: a contradiction inside one schema
  does not resolve as the newer half winning, and asked for an Office dashboard
  the model took neither branch — it appended an Office page to an unrelated
  dashboard and reported it created. The description now says what it does and
  points at `create_dashboard` for the rest; a test holds the whole dashboard
  family to denying nothing. The result carries a `url` for the same
  reason: a page appended to an existing dashboard is unfindable from a bare
  "created" — percent-encoded, because Lovelace validates nothing and a path
  stored as `kitchen#lights` would otherwise produce a link the browser reads as
  a fragment. The view stores fine either way; only the link would be wrong,
  which is the worst shape for a result whose whole job is to be followed, and it is empty until cards are added, which is the other half of
  "I can't see anything". A genuinely separate dashboard is `create_dashboard`.
- **Nothing here can ask Home Assistant whether a card is valid.** Lovelace has
  no server-side validator: it stores what it is given and the frontend finds
  out, rendering "Unknown type encountered: fan" on the user's wall. An
  automation gets `async_validate_config_item`; a card gets three things instead,
  and each was added after a broken card reached a real dashboard.
  - **A domain used as a card type is refused.** There is no `fan`, `switch` or
    `cover` card. The check is INVERTED rather than an allowlist of HA's card
    catalogue: that goes stale every release, can never contain a home's custom
    cards, and every gap in it is a valid card refused. So it asks "is this the
    name of a domain in THIS home that has no card of its own?", reading the live
    state machine, because that is the shape the mistakes take — the model draws
    on the home's own vocabulary. What is left to maintain is
    `_DOMAIN_NAMED_CARDS`, twelve names that change about once a year. A card
    type newer than this code passes, every `custom:` card passes, and a domain
    the home does not have passes — a fanless home cannot catch `fan`, which is
    the honest price of not maintaining a catalogue.
  - **The check walks the whole card**, the way `_unknown_entities` does. The
    wrong type is usually a CHILD: a valid `grid` holding one bad tile. It
    follows `cards`, `sections` and the conditional card's singular `card`, and
    deliberately NOT `features` — a tile's features carry their own vocabulary
    (`light-brightness`, `fan-speed`) which are not cards, and walking them would
    refuse every tile that has one.
  - **The vocabulary is handed over before composing** (`dashboard_cards.py`), on
    `get_dashboard`'s result — the read the model is told to always do first — so
    it arrives once per turn rather than on every write. A refusal only says what
    NOT to write; this is the cheaper half. Kept short: it shares the 16K result
    budget with the dashboard the model actually asked for, and card OPTIONS are
    omitted because the model knows Lovelace's schemas — what it cannot know is
    which types exist here.
- **A refused write is never reported as done.** The refusal goes back as an
  ordinary tool result, so the model can correct itself and call again inside the
  turn — that is the same loop an invalid automation gets, without a bespoke
  retry path, because the write is a tool call rather than a block in the final
  answer. What was missing is the other end: nothing stopped a turn whose writes
  were ALL refused from answering "Added the card". `note_failed_dashboard_write`
  states the outcome and sets `validation_error` / `validation_target` so the
  panel can tell a claimed change from a real one without reading prose. Only
  when nothing succeeded — a turn that fixed itself on the second call did what
  was asked — and writes only, since a failed read is answered by the prose.
- **Deleting a dashboard is the panel's job too**, for the reason creating one
  is: `DashboardsCollection` is published only to lovelace's admin-only websocket
  commands. Same shape — the server validates and proposes a closed intent, the
  panel performs it. HA keys the delete by the collection **id**, not the
  `url_path` the user named, so the panel resolves that from the list it already
  fetches; an already-deleted dashboard reports DONE, because a retry after a
  lost report finds exactly that and it is the outcome that was asked for. The
  card says **Delete**, in the deny tone: wording and tone follow the ACTION,
  not the kind of card. A YAML dashboard is refused with where to change it
  instead.
  - **Neither handle is immutable, so the descriptor carries a third.**
    `DashboardsCollection._get_suggested_id` returns the `url_path`, so a
    dashboard's id IS its path and deleting one frees BOTH at once: make
    another at that path between the proposal and the tap and the replacement
    answers to every handle the panel matches on. The intent therefore carries
    an `expected` block — title, icon, `require_admin`, `show_in_sidebar` — and
    the panel compares it through the same `matchesProposal` the create uses to
    recognise its own retry. Nothing a dashboard carries is immutable enough to
    be a true identity; a recreation made identical in all four is one the user
    could not distinguish either. The raw stored title goes in that block, not
    the card's `title`, which is sanitized and truncated for display and would
    mismatch any dashboard named at length. A card with no `expected` predates
    the field and still deletes — a proposal can outlive a deploy, and failing
    on a field nobody sent is not a guard.
  - **The default is refused by IDENTITY, not by name.** `/default` is a path a
    user can genuinely have — single-word paths are allowed and the create tool
    here makes them — so reserving the string left that dashboard undeletable
    while telling its owner it was the built-in Overview. The target is
    resolved, then compared against what `_lovelace_dashboard(hass, None)`
    resolves to, which already collapses `""`, `None` and `"lovelace"` onto
    whichever key HA is serving. Asking the one resolver twice is what keeps
    the two answers in agreement.
  - **The blast radius is counted, and UNKNOWN is not zero.** HA drops the
    stored document with the entry, so every view and card goes. An unreadable
    document does NOT block the delete — it is read for the label only, and
    refusing would strand the user with a dashboard nothing here can remove —
    but `_load_or_reason` returns nothing precisely when the dashboard renders
    content we cannot enumerate, and a generated Overview is covered in cards.
    So the counts are OMITTED rather than zeroed and the card says contents
    unknown: a false blast radius is worse than no number, because it invites
    the tap. The count that is reported includes cards inside containers
    (`_cards_in_tree`) — `_flat_cards` yields what a card index can NAME, which
    is the right question for editing and the wrong one here, since a grid
    holding twenty tiles is one addressable card and all twenty go with it.
- **A dashboard turn is LINKED, not tiled.** Entity tiles render as live HA cards
  and read as a preview of the layout that was saved, which they are not, so
  `is_dashboard_turn` strips them — and the chat handler used to append tiles of
  its own further downstream, putting them straight back. Each half was right
  alone, so no test of either half could see it and the rule was documented and
  not in force; it is one question now, asked once and honoured by both. What
  goes there instead is `[[dashboard:<url>|<label>]]`, a marker the panel renders
  as its own card — a page is not a device, so it must not look like the card
  that toggles a light. Emitted regardless of what the prose says, because the
  model writes the path as a code span ("available at `/office/0`") which looks
  like a link and is not one; only an existing marker suppresses it. The pattern
  accepts a single-slash absolute path ONLY, since the marker travels through
  model-authored text.
  - **Every page the turn wrote, or none.** A turn that adds four views writes
    four urls, and `_dashboard_targets_from_log` used to keep the last one it
    saw — so three pages the reply named by path had no card, and the one card
    read as "here is the page I made" rather than as one of four. Deduplicated
    by url, since moving three cards onto one page reports it three times, and
    the label is per card so an untitled page cannot blank the others. The
    suppression is **per page** (`_already_linked`), not "the text already has
    a marker": the marker family is in the prompt and a session replays its own
    earlier assistant turns, so a model writing one for the page it is
    narrating is ordinary — and one blanket check discarded the other three,
    which is this same failure through a different door. Terminated on the `|`
    or `]]` rather than matched as a prefix, since `/lovelace/0` is a prefix of
    `/lovelace/01`. Past
    `_MAX_DASHBOARD_LINKS` (5) the cards stop being navigation and none are
    emitted: the prose already names them, and picking a subset is the bug.
    The card is **inline-flex**, so the `<br>` between two markers is what
    stacks them — `markdown.js` keeps exactly one break between links and drops
    the ones above the first, where the card's own top margin provides the gap.
    Stripping every break, as the block-level entity grid does, lays the cards
    out side by side and wraps them mid-row.
- **Lovelace validates nothing server-side.** The stored document is free-form
  JSON owned by the frontend, so a view's `title` and `path` are **not** unique.
  `resolve_view` accepts an index, a path, or a title, and **refuses an ambiguous
  name** with the candidate indices — the index is the only guaranteed handle.
  Ambiguity is collected **across both fields at once**, not field by field: a
  name can match one view's title and a *different* view's path, and checking
  `path` first and returning on its single hit resolves that silently — to the
  view the user was least likely to mean, since they named it by the label the
  sidebar shows. Two fields on the *same* view is one target, not a collision.
- **A sections view keeps cards somewhere else.** A classic view holds them at
  `view["cards"]`; a `type: sections` view holds them at
  `view["sections"][n]["cards"]` and ignores a top-level `cards` key entirely.
  Cards are therefore addressed by a **flat index across every card list in the
  view** (`_flat_cards`), so a caller never has to know which section it is
  looking at. `add_dashboard_view(sections=True)` seeds one grid section, because
  a sections view with none silently drops the first card added to it.
- **A dashboard's own `require_admin` is enforced per read.** HA registers no
  panel for such a dashboard for a non-admin, so it is invisible to them in the
  UI — while these read tools are deliberately available to non-admin chat users
  and read-only MCP credentials, and were handing back its full card
  configuration. `_hidden_from_caller` reports it as ABSENT rather than refused:
  a distinct "you may not read that" confirms the dashboard exists, which is the
  one bit HA is withholding. It is dropped from `list_dashboards` and from the
  "Available:" list in the not-found error for the same reason.
  Identity reaches it through `helpers.CALLER_IS_ADMIN`, a ContextVar opened by
  `caller_scope` at both dispatch sites (`ToolExecutor.execute`, the MCP
  `call_tool` handler) — both handler signatures carry no identity, and
  threading a flag through every handler to serve the few that need it is worse.
  It defaults to False, so a call that never opens the scope gets LESS access.
  `requires_admin` on a `ToolDef` gates the tool; this gates the object, which a
  tool allowlist cannot express.
  **A confirmation card's second leg has to re-open the scope.** It runs from
  `_handle_websocket_resolve_approval` long after the `ToolExecutor` scope that
  built the card has ended, so the ContextVar has reverted to its deny-by-default
  `False` — and the removal is then refused on behalf of the very admin who just
  tapped confirm, as "No dashboard". Any new post-confirmation execution path
  needs the same wrapper. It reads `connection.user.is_admin` rather than passing
  `True`: `_require_admin` above it means only an admin can get there today, and
  reading it keeps that a fact about the gate instead of an assumption copied
  into a second place.
- **`ConfigNotFound` from a storage dashboard means AUTO-GENERATED, not empty.**
  `LovelaceStorage.async_load` raises it while the stored config is None, and
  the frontend is meanwhile rendering the original-states strategy — the user is
  looking at a full Overview. Reading that as `{}` reported zero views for a page
  covered in cards, and let `add_dashboard_view` save a document holding only the
  new view, replacing everything the user could see. The generated config cannot
  be materialised here: the strategy runs in the frontend and core ships no
  server-side generator (the `map` dashboard is seeded by *writing* a strategy
  config, not by rendering one). So `_load_config` returns `_AUTO_GEN_NOTE` as an
  error and points the user at Take control, which is HA's supported one-click
  way to turn the generated page into a stored one. Guarded inside `_load_config`
  rather than at each caller so every existing tool and every one added later
  inherits it — the cost is that building out a brand-new dashboard needs that
  one click first, which is the honest trade against overwriting a live Overview.
  Two writers do NOT go through `_load_config` and are guarded separately:
  `async_insert_card`, and `recipes.dashboard.async_place_card`, which seeds its
  own one-view `Home` document on `ConfigNotFound` and is called directly by the
  recipe pipeline. `ConfigNotFound` is ambiguous — a genuinely blank dashboard
  raises it too, and seeding is right there — so both probe rather than assume.
  The probe fails **closed**: it is the only thing between a transient storage
  error and a document written over a live Overview, and the callers are already
  in a `ConfigNotFound` branch when they ask, so a second failed read is not
  evidence there is nothing to lose.
- **`None` is not "the default dashboard" — `"lovelace"` may be.** HA is migrating
  the default Overview off the `None` key onto a real entry keyed `"lovelace"`
  (`_async_migrate_default_config` moves the stored config and repoints the
  default panel), and a YAML-mode install registers its `LovelaceYAML` under the
  same key. `dashboards[None]` survives either way as an empty `LovelaceStorage`
  that HA registers no panel for. `helpers.default_dashboard_key` resolves an
  unqualified target the way HA does — prefer `"lovelace"`, else `None` — and
  both `dashboard_manager` and `recipes/dashboard.py` route through it, or an
  unqualified read shows an empty home and an insert lands on a dashboard the
  user cannot see. `list_dashboards` hides the leftover placeholder for the same
  reason.
- **`async_load` hands back HA's own cached config, so `_load_config` DEEP-copies.**
  `dict()` copies only the root mapping — the `views` list and every view and
  card inside stay the live objects HA is serving. Writers here mutate before
  they validate, so a shallow copy lets a *rejected* edit stick: rename a view,
  hit the duplicate-path check, return an error, and the cached title has
  already changed for the next save by anyone to persist. Readers copy too, or
  the card returned is a live reference that anything trimming the tool result
  shortens in place. `recipes/dashboard.py` copies the same way, for the same
  reason.
- **`list_dashboards` covers every dashboard and is not admin-gated.** It is the
  discovery half of the READ tools, which are themselves non-admin and read YAML
  boards fine — gating it on admin left a non-admin able to read the default
  dashboard and no other, and dropping YAML boards left a user staring at a
  dashboard in their sidebar that Selora insisted was not there, with no way to
  learn the `url_path` that would have fetched it. `editable` on each row is
  what keeps it usable as a placement picker, and it reports whether THIS CALLER
  could write (`CALLER_CAN_WRITE`, **not** `CALLER_IS_ADMIN` — `_check_tool_access`
  lets a custom MCP token with an explicit allowlist, or a JWT carrying the write
  scope, mutate without being an HA admin, and one shared boolean called those
  dashboards read-only while their writes succeeded). MCP answers it with
  `_can_access_tool` across **every** dashboard mutation, derived from
  `_DERIVED_MCP_TOOLS` rather than listed — an allowlist naming only `insert`
  authorises a real editing workflow, and a new mutation must be covered without
  anyone remembering to name it rather than what mode the
  dashboard is in — every mutation tool is
  admin-gated, so a read-only credential told `editable: true` is offered a
  workflow it cannot finish — a fresh install's
  Overview is storage-mode and still generated, so a mode-only answer invited a
  workflow every write then refused.
  `recipes.dashboard.list_writable_dashboards` stays as it is: it backs the
  recipe wizard's card-placement picker, where a board that cannot be written to
  is genuinely not a choice.
- **`DASHBOARD_LOCK` lives in `helpers.py` and is shared with `recipes/dashboard.py`.**
  Both write whole documents, so a module-local lock leaves the overlap that
  matters open: a recipe install or uninstall landing between this module's load
  and its save silently discards the edit, and vice versa. `async_place_card`
  holds it across load→mutate→save; `async_remove_cards` holds it for its entire
  multi-dashboard sweep, since releasing between boards would let an edit land
  on one already read but not yet written. One lock for every dashboard rather
  than one each: the writes are rare and sub-millisecond, while a per-target
  registry has to key on something — and the default dashboard answers to
  `None`, `""`, and `"lovelace"` at different call sites, so the key is the part
  that would get it wrong.
- **A tagged card can be nested, and a re-install refreshes it IN PLACE.**
  `group_dashboard_cards` wraps existing cards in a container, and organising a
  recipe's card is an ordinary thing to do — so `_replace_tagged` recurses
  instead of scanning only the lists `_view_card_lists` returns. Flat scans made
  re-install add a second copy rather than replace, and left uninstalled cards on
  the dashboard with nothing remaining that knew they belonged to a recipe.
  Recursing is only half of it: purging and then appending dedupes correctly and
  *still* undoes the grouping, moving the card back to the end of the view with
  nothing to say why. So the replacement lands on the first tagged card found,
  wherever it sits (`replace_tagged_card`), any further ones are dropped, and
  only a genuinely new card is appended. `purge_tagged_cards` is the same walk
  with no replacement, which is uninstall. A container emptied *by* the removal
  goes with its contents; one whose card was substituted is not empty and
  survives untouched; one still holding a user's own card stays.
  Note what a dedup-only test cannot see: it passes for both behaviours, so the
  layout assertion is the one that discriminates.
- **The Lovelace UI writes this same document.** Saving is read-modify-write of
  the whole config and the lock covers only writers we own, so a card
  index captured in one call means nothing by the next. Every card edit carries a
  content fingerprint (`card_fingerprint`) re-checked against the freshly-loaded
  document immediately before the save, and **every view-index mutation carries a
  `view_fingerprint`** for the same reason — a view has no id and its index
  shifts when an earlier one goes. That covers removal, `update_view` (a rename
  landing on the wrong page is quieter than a deletion: nothing disappears, so
  nobody looks), `group_cards` (whose card indices are *all* relative to the
  view as it was read, so a stale view invalidates every one of them at once)
  and `move_card` — whose card fingerprint pins only the SOURCE, leaving the
  destination index free to mean somewhere else by the time it lands.
  `get_dashboard` hands the fingerprint out per view, which is what makes the
  guard usable at all — a write cannot demand a token the read never returns. It
  is a *content* hash, not a card count: counts collide, so a reorder
  between the card and the tap would pass a count check and delete a different
  page. Removal then drops the resolved **object**, not the index, because
  `resolve_view` indexes the dict-only view list while the stored `views` list is
  free-form and may hold a stray non-dict ahead of the target.
- **A dashboard's title is metadata, not document.** `async_load` normally
  returns just `views`; the title is on `config.config`. Both `get_dashboard`
  and `list_dashboards` read it through `_dashboard_title`.
- **`_load_or_reason` is the single classifier.** `_load_config` and
  `list_dashboards` both ask it, so `editable` cannot advertise a dashboard every
  write then refuses — the same bug in either direction. `async_place_card` loads
  its own document and checks `is_strategy_document` directly, since the recipe
  pipeline never goes through `_load_config`.
- **A missing YAML file is a read error, not an empty dashboard.**
  `LovelaceYAML.async_load` raises `ConfigNotFound` while its mode stays `yaml`,
  so the auto-gen probe says nothing about it — but `async_get_info` returns an
  `error` key naming the path. Reporting zero views instead hides a
  configuration problem behind a page that looks merely empty.
- **A stored `strategy` is not an empty dashboard either.** The built-in Map and
  friends store `{"strategy": {...}}` and no views, and `async_load` SUCCEEDS —
  so unlike the auto-generated case nothing else notices. A saved `views` list
  then sits in the document while the frontend keeps building from the strategy
  and ignoring it: the edit reports success and never appears. `_load_config`
  refuses, which covers reads too — "0 views" would be a lie about a page the
  user can see.
- **Setting and clearing a view field need separate arguments.** `_opt_str`
  treats a blank string as absent everywhere here, precisely because models fill
  unused optional params with `""`; reading that as "clear" would strip the icon
  off any view updated by a model that padded its arguments. So
  `update_dashboard_view` takes a `clear` list naming fields to remove — the
  same delta shape the group tools use, and for the same reason.
- **YAML dashboards are readable, not writable.** They are reported with
  `editable: false` and a note rather than as missing — the user can see them in
  the sidebar, so "no such dashboard" would read as our bug rather than as a
  property of their setup.
- **Reordering and grouping are separate primitives, and both are needed.**
  `insert_dashboard_card` only ever appends, so `move_dashboard_card` is the only
  way to reorder. And a masonry view has **no rows** — cards flow into columns —
  so "put these three side by side" is a *container*, not an ordering:
  `group_dashboard_cards` moves them into one. Both move the card OBJECTS; a
  caller that rebuilds a card from a summary loses whatever it did not think to
  copy. The container is the CALLER'S card config, passed through with only
  `cards` filled in — the model knows Lovelace's card schemas, so enumerating
  container types and their options here would be a second, staler copy of that
  knowledge, and every option not thought of would be unreachable.
- **"Move all the media" is one call, and the read has to make that possible.**
  Two halves of the same failure: asked to move the media cards off Overview,
  the model spent every tool round on `get_dashboard_card` and answered that it
  could not do it safely.
  - **A card summary names the entity domains the card shows**
    (`_card_domains`, walking the whole card via the `_card_entity_ids` that
    `_unknown_entities` also uses). The TYPE does not answer "which are the
    media ones?" — `tile` and `entities` are the two commonest types and both
    are domain-agnostic — and neither does a title. Without it the only way to
    tell is one fetch per card, which on an ordinary 18-card view exhausts
    `MAX_TOOL_CALL_ROUNDS` before anything moves and the forced final round has
    to answer from reads alone. Absent rather than `[]` on a card that shows no
    entity, and capped at `_MAX_CARD_DOMAINS`. `get_dashboard_card`'s
    description now says it is NOT needed to move or remove a card.
  - **`move_dashboard_card` takes `from_indices`.** Repeating the single-card
    call is not equivalent: taking one card out shifts every later index in
    that view, so the second call works from indices that stopped meaning what
    the caller read, and a stale index silently moves whichever card now sits
    there. One call also keeps the cards' relative order, lands them
    contiguously, and costs one round rather than one per card. Removal walks
    the wanted indices **highest first** so the positions still to be reached
    stay valid — the same reason `group_dashboard_cards` does.
    `expected_fingerprint` describes ONE card and is **refused** alongside
    several (`expected_view_fingerprint` pins every index at once); an empty
    `from_indices` is ABSENT, per the empty-optional rule, while both spellings
    disagreeing is refused because each says which cards to move. `from_index`
    is therefore optional, which makes `_opt_index` load-bearing in the
    executor — `_as_index` would coerce its absence to 0 and move the view's
    first card alongside the ones that were asked for.
    `_disk_fingerprints` / `_view_fingerprints` count a whole view off ONE read
    of the file, because the destination-first landing check now has as many
    fingerprints to confirm as the caller named.
  - **The argument coercion is shared with MCP** (`move_card_kwargs` in
    `tool_executor`). That handler was a hand-written second copy, which is the
    drift this file warns about twice — quietly, where the MCP client rejects a
    move chat accepts.
- **A move crosses views and dashboards, and it has to be one primitive.**
  `move_dashboard_card` takes `to_dashboard` / `to_view`; omitted, they mean the
  view the card is already on, and for another dashboard, its first page — a
  transfer names the dashboard, and which page it lands on is rarely the point.
  `to_index` is therefore optional for a transfer (append) and still required
  for a reorder, where there is nothing to default to. `get_dashboard_card` +
  `insert_dashboard_card` + `remove_dashboard_card` only LOOKS equivalent: the
  caller re-serialises the card in between, and the caller is an LLM working
  from what it was shown — it drops whatever it did not think to copy, and a
  card too large to fetch intact cannot be moved at all. That composition also
  has no safe ordering, where the primitive does: **destination saved first**,
  so a half-completed transfer duplicates the card (visible, undoable) instead
  of losing it. The source/destination comparison is by resolved config
  IDENTITY, never the argument strings — the default dashboard answers to
  `None`, `""` and `"lovelace"` at once, so a reorder naming `lovelace` would
  otherwise load that one document twice and save the second copy over the
  first. A destination refusal is prefixed as the DESTINATION's: both
  dashboards are refused in the same words, and unmarked the caller retries
  against the source it was never told was fine. **A transfer has TWO views to
  pin.** `expected_view_fingerprint` covers the source; on a same-view move the
  two are one object so it answers for both, and the moment the destination is
  elsewhere that guarantee is silently gone — while it is the DESTINATION that
  carries `to_index`, on a dashboard the caller read in a separate call.
  `expected_to_view_fingerprint` is the other half, checked before either
  document is mutated so a stale destination cannot leave the card pulled out
  of the source. **Both saves pass their pre-mutation snapshot to `_save`,
  which is what makes those two error sentences true rather than merely
  intended.** A save that RAISES has already taken effect everywhere except
  the file — `LovelaceStorage.async_save` replaces its cached config and fires
  the update event *before* it awaits the store write, and `async_load` serves
  that cache rather than re-reading — so without the rollback "the card was not
  moved" is said over a destination already showing it, and "it now appears on
  both" over a source it had just vanished from. Re-saving the original puts
  the cache and the event back whether or not its own write lands, since both
  happen ahead of the await. `_save`'s `previous` is opt-in, so every other
  writer here keeps its existing behaviour.
- **Destination-first protects nothing unless the destination is known to have
  LANDED.** A `_save` that reports success has only been ACCEPTED:
  `Store.async_save` does not raise on an ordinary write failure —
  `_async_handle_write_data` catches `WriteError` / `SerializationError`, logs
  and returns — and it skips the write outright when the store is read-only or
  HA is stopping. So the commonest failures are silent, and reading the
  dashboard back settles nothing, since `async_save` updated that cache before
  attempting the write. Removing the source on that basis LOSES the card: both
  halves look fine until the next restart, when it is on neither. So a
  cross-dashboard move confirms the destination against the FILE
  (`_disk_fingerprints`, through the private `Store` — the only thing that
  answers) before saving the removal, and rolls the destination back if it did
  not land. **Counted, not tested**: a destination already holding an identical
  card answers "is it there?" off the copy that was already on disk. The source
  is checked the same way afterwards, but only to report — a silent failure
  there costs a duplicate, not the card, and undoing a destination that did
  land would be worse. An unreadable or unexpected `Store` is UNKNOWABLE and
  treated as fine: refusing every move on an HA whose internals have moved is
  worse than the case it guards.
- **A guard about which dashboard must not be phrased around what was asked
  for.** `add_dashboard_view`'s "not a dashboard" warning is scoped to
  appending to some OTHER dashboard; giving a just-created one its first page is
  the tool's job and it says so. Phrased around the ASK, it fires on exactly the
  turn that must not obey it: a resumed turn replays a request opening "create a
  new X dashboard", so asked to move cards onto the dashboard it had just made,
  the model read the guard as forbidding the view, found `create_dashboard`
  already done, and asked the user to add the page by hand. `resolve_view`'s
  empty-dashboard error names `add_dashboard_view` for the same reason — a bare
  statement of the fact is something the model relays as a step for the USER to
  perform. The result note carries no such guard at all: a pre-call decision
  restated after the call only ever reaches a turn that already chose right.
- **Card writes validate entity ids.** Lovelace stores whatever it is given, so a
  typo'd entity is saved happily and renders "Entity not found" on the user's
  wall panel with nothing else to catch it. `_unknown_entities` walks the whole
  card — an id can sit in `entity`, `entities`, a nested stack's `cards`, or a
  tap action — and the write is refused with the missing ids named. The walk
  carries a list's parent key down to its elements, so the commonest spelling of
  all, `entities: ["light.one"]`, arrives keyed `entities` rather than `entity`
  and needs its own branch. Strings there are shape-checked against
  `_ENTITY_ID_RE` first — and so are `entity`/`entity_id`, because a custom card
  may hold a TEMPLATE where an id goes (button-card's `[[[ return … ]]]`, or
  Jinja) and that is a valid card the home renders, not a typo; the state lookup
  finds no such entity and refuses the whole write. Likewise a row in an
  `entities` list may be a plain label or a divider. A typo worth catching is a
  typo in an entity id, which always has a domain and a dot.
- **A move is `pop(from); insert(to)`.** The destination is re-flattened after
  the removal, because pulling the card out shifts every later index — including
  the one the caller named. What it must NOT do is then add one for a forward
  move: that lands the card *after* the destination, so moving 0 → 1 in
  `[A, B, C]` gives `[B, C, A]` instead of the `[B, A, C]` that was asked for.
  Only an index past the last remaining card means the end of the view.
- **A dashboard turn drops its entity tiles.** `[[entities:…]]` markers render as
  real HA cards grouped under `### Area` headings, which is a good answer to
  "which lights are on?" and a misleading one straight after a dashboard edit,
  where it reads as a preview of the saved layout.
  `strip_entity_tiles_after_dashboard_turn` removes them in
  `synthesize_approval_from_tool_log` — the one funnel both chat paths pass
  through. The prose stays.
- **Reads are bounded and one view at a time.** Cards sit inside a list of view
  dicts, which `ToolExecutor._find_longest_list` cannot reach, so an oversized
  dashboard would have a whole *view record* popped rather than being trimmed.
  `get_dashboard` returns cards only for the view you name.
- **A card too big to return whole is refused, not truncated.** `get_dashboard_card`
  measures the ASSEMBLED result against `_MAX_CARD_CHARS` and errors out rather
  than handing back a fingerprint. `_truncate_result` trims the longest list —
  an `entities` list, a nested stack's `cards` — while `card_fingerprint`
  describes the whole card, so the shortened copy passes the identity check on
  write-back and silently deletes every trimmed row. The caller is an LLM
  editing what it was shown and cannot tell that rows went missing. Omitting
  just the fingerprint is not enough: `expected_fingerprint` is optional on
  `update_dashboard_card`, so the unpinned write would still land.
- **`add_dashboard_view` reports an index off the FILTERED list.** The stored
  `views` list is free-form and may hold a stray non-dict, while every reader
  here indexes the dict-only sequence — a raw `len(views) - 1` is reported back
  and then rejected as out of range by the next call that uses it.
- **A confirmation-gated tool says so on MCP.** The shared description is written
  for chat, where the preview returns and the user taps a card. MCP has none —
  the handler runs the write on the spot — so a description promising a card is
  a guarantee an agent will act on, and the view is gone before anyone is asked.
  `_mcp_tool_from_chat_tool` appends the correction for any tool in
  `_DELETE_TOOLS` / `_DESTRUCTIVE_TOOLS`, derived from the same allowlists the
  previews use so a tool added there cannot forget it.
- **`remove_dashboard_view` takes `expected_fingerprint` on both surfaces.** Chat
  routes it through the confirmation card, which carries the fingerprint in its
  descriptor and re-checks it at confirm time, so the parameter looks redundant
  there. MCP has no such card — its handler deletes on the spot — so leaving the
  parameter off the shared `ToolDef` made the guard unreachable for exactly the
  caller that had nothing else protecting it. Every MCP schema here is *derived*
  from the chat `ToolDef`, so a write path MCP reaches directly has to have its
  guard expressed in that shared definition. The chat preview honours it too,
  which catches the mismatch before the user taps confirm on a card naming the
  wrong page rather than after.
- **`list_dashboards` and `insert_dashboard_card` are on MCP too.** Every other
  MCP dashboard tool's description sends the client to those two — the first to
  learn a non-default `url_path` for `dashboard_target`, the second because all
  the rest EDIT a card that is already there, so a view made with
  `selora_add_dashboard_view` could never be filled. Their bodies are shared
  rather than restated: `async_insert_card` in `dashboard_manager.py` holds the
  validation and the `view` coercion, and both surfaces call it, for the same
  reason the MCP schemas are derived from the chat `ToolDef`s. A second copy
  drifts on the next argument added, quietly, in the shape where the MCP client
  rejects a card chat accepts. When adding a dashboard tool, check `_ADMIN_TOOLS`
  / `_READ_ONLY_TOOLS` agree with the chat `ToolDef`'s `requires_admin` — a read
  tool in the admin set is unreachable for a read-only credential, and a write
  tool missing from it is reachable by one. `tests/test_dashboard_tools.py`
  asserts that correspondence.
- **A stored card need not be a dict.** `_flat_cards` yields whatever is in the
  list, because Lovelace storage is free-form, so any path that calls a mapping
  method on a card needs an `isinstance` check first — otherwise the failure is
  an `AttributeError` surfacing to the user as "Tool execution failed" instead
  of the error the code meant to return.
- **Every dashboard tool is in BOTH tool lanes.** "Add a card to my dashboard"
  classifies as `command`, "reorganise my dashboard" as `config`, and the same
  tools serve both. Before this they were in *neither* lane, so a
  command-classified turn could not see `insert_dashboard_card` at all. Putting
  the family in both is deliberate: the alternative is another vocabulary
  heuristic choosing a lane, and that decision has been wrong repeatedly.

## LLM Providers

| Provider | Config Key | Default Model | Notes |
|----------|-----------|---------------|-------|
| Selora Cloud | — (OAuth linking post-setup) | — | Cloud, Selora-hosted, **default provider** — no API key |
| Selora AI Local | `selora_local_host` (`http://localhost:8080`) | Selora AI (Qwen3 1.7B + LoRAs) | Local, on-device via llama-server, no API key |
| Anthropic | `anthropic_api_key` + `anthropic_model` | `claude-sonnet-4-6` | Cloud |
| Google Gemini | `gemini_api_key` + `gemini_model` | `gemini-2.5-flash` | Cloud, uses native REST API (not OpenAI-compat) |
| OpenAI | `openai_api_key` + `openai_model` | `gpt-5.4` | Cloud, OpenAI chat completions format |
| OpenRouter | `openrouter_api_key` + `openrouter_model` | `anthropic/claude-sonnet-4.5` | Cloud, OpenAI-compat gateway |
| Ollama | `ollama_host` + `ollama_model` | `llama4` at `host.docker.internal:11434` | Local, no data leaves network |
