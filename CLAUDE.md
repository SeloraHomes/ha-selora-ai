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
- **How repairs are recorded:** pure helpers call `record_repair("<type>")` (a no-op outside an LLM call). `UsageTracker.scope` opens a per-call ContextVar buffer and drains it at the call boundary, where provider/model are known, then POSTs fire-and-forget (never raises). To add a repair counter: add the type to `REPAIR_TYPES`, call `record_repair(...)` at the repair site, cover it in `tests/test_telemetry.py`. The instrumented paths: `service_name_inference`, `state_info_strip`, `trailing_marker_reposition`, `friendly_name_strip`, `tool_markup_leak` (`llm_client/parsers.py`, `command_policy.py`), `qwen_normalize` (`providers/_qwen_repair.py`), `cloud_json_salvage`. `tool_markup_leak` fires when a model emits its tool-call syntax as plain text (`<invoke …>` / mangled `< | DSML | …`) instead of a real tool_use block — stripped non-streaming by `strip_leaked_tool_markup` and mid-stream by `MarkupLeakGuard` in `llm_client/parsers.py`, both called from the tool loop in `llm_client/client.py`.
- To add a snapshot count: add the key to `_SNAPSHOT_PROPERTY_KEYS` and populate it in `TelemetryClient._gather_snapshot`.
- To add an activity counter: add the name to `_ACTIVITY_COUNTER_KEYS`, call `record_activity(hass, "<name>"[, n])` at the action's chokepoint (instrumented sites today: `automation_store.add_version`/`purge_record`, `automation_utils.async_toggle_automation`, `scene_store.async_add_scene`, `pattern_store.save_pattern`/`save_suggestion`/`update_suggestion_status`, the chat/command handlers + `_execute_command_calls` in `__init__.py`, `conversation._async_handle_message`, `device_manager.discover_network_devices`/`_count_if_paired`, `llm_client/usage.flush`, `providers/base._emit_quota_exceeded`), and cover it in `tests/test_telemetry.py`. The helper never raises and counts even when opted out (the flush is what's gated).

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
