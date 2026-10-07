# Selora AI — Home Assistant Integration

> Read by AI coding assistants on every session, so it holds only what applies
> repo-wide. Record a rule and its reason, not the story of how it was found
> (that belongs in the commit message), and nothing the code already says — what
> a module does belongs in its docstring, setup and deployment in
> `CONTRIBUTING.md`, a single feature's rules in `docs/dev/`.

A custom integration (`custom_components/selora_ai/`) that analyzes a home's
devices and usage with an LLM (Selora Cloud, Selora AI Local, Anthropic, Gemini,
OpenAI, OpenRouter, Ollama), proposes automations, scenes and dashboards, and
takes natural-language commands from its own panel, from Assist, and over MCP.

## Conventions

### Python
- Python 3.14 (CI also runs 3.13), async throughout, `from __future__ import annotations` in every file.
- Fully typed, modern syntax (`str | None`). Use the TypedDicts in `types.py`
  rather than `dict[str, Any]`; bare `Any` only for genuinely dynamic data (raw
  external JSON, HA store loads). Annotation-only imports go under `TYPE_CHECKING`.
- `_LOGGER = logging.getLogger(__name__)`.
- Catch specific exceptions, never bare `except Exception`.
- No secrets in code — API keys come from the config entry.
- `uuid.uuid4()` for ids, never `hashlib.md5` (SAST flags it).
- Don't import `dataclasses.field` unless it is used.

### Home Assistant
- Config entries carry `entry_type`: `"llm_config"` or `"device_onboarding"`.
  The first entry configures the LLM; "Add Entry" goes straight to device discovery.
- Config-flow step ids must match keys in `strings.json`.
- Entities use `_attr_has_entity_name = True` and reference the hub device
  `(DOMAIN, "selora_ai_hub")`.
- Lovelace is written through its API (`LovelaceStorage.async_save`), never by file.
- Never auto-accept discovered devices; never auto-configure an API key.
- **A startup deferral is a TIMER, never a task that sleeps out its own delay.**
  `hass.async_create_task` registers the task, so bootstrap, every entry reload and
  every test's `async_block_till_done()` wait for it — `await asyncio.sleep(120)`
  in a task made a 400ms test take 120s, and checking the feature flag after the
  sleep does not help. Arm with `async_call_later`, fire the work with
  `async_create_background_task` (which `async_block_till_done()` does not wait
  on), cancel the timer on unload before the first `await`, and let the callback
  tolerate a torn-down entry. Patterns: `health_monitor.py`, `insights_audit.py`,
  `insights_export.py`. `tests/test_startup_delays.py` guards by TIMEOUT — a
  regression makes a test slow, not red.

### Git
- Branches `selora-ai-<feature>` off `main`; conventional commits.
- `fix:` / `feat:` cut a release and show users a changelog line; CI, tooling and
  release plumbing are `chore:`.

### Manifest requirements

HA installs our `requirements` with core's `package_constraints.txt` as
`--constraint`, so **core's pin decides the version** and our specifier only
decides whether the install *succeeds*. An unsatisfiable one means the
integration never sets up (`Requirements for selora_ai not found: ['PyJWT>=2.13.0']`)
while the entry, files and HACS all look healthy.

- **Never declare a package core itself requires** (e.g. `PyJWT`) — a floor can
  only break the install. A Renovate bump once gave a release an implicit
  core ≥ 2026.8 floor and every 2026.7.x hub silently failed to load.
- **Test the declaration, not the newest core.** Hubs don't auto-update core, so
  `tests/test_manifest_requirements.py` refuses any package core owns — required
  by core (`Requires-Dist`) or `==`-pinned in its constraints.
- **A package we need above core's pin is a blocker, not a floor** — declaring
  fails the install, omitting fails at runtime.
- **A package core merely constrains is checked on the INTERSECTION**, both
  directions. Where PEP 440 gets fiddly (`~=`, `===`, `==6.4.*`) the check is
  conservative, since a false alarm blocks an MR. It reads the installed core, so
  a bound only an older core had is a blind spot.
- That test is the only guard (CI test jobs `pip install` directly), and it can't
  live in `scripts/validate_manifest.py`: the `validate` job has no core installed.

### i18n

**Backend strings**: `strings.json` is the English source; `translations/<lang>.json`
mirrors it for all 13 locales (`en fr de es it nl hu pt ru ja ko zh-Hans zh-Hant`).
A new key goes into every file in the same commit (hassfest fails otherwise).
Keep placeholders (`{count}`, `{device_list}`, …) verbatim.

**Reply language** is resolved per turn by `resolve_reply_language()`
(`llm_client/lang_detect.py`): detected message language (marker sets for
fr/de/es/it in `_MARKERS`) → panel locale → `hass.config.language`. It drives
both `_language_directive()` and the deterministic confirmations, so a French
command on an English UI gets a French reply. `architect_chat` /
`architect_chat_stream` reassign their local `language` to it at entry. English
and unsupported scripts detect as `None` and fall back to the panel locale.

- `_LANGUAGE_NAMES` (`llm_client/prompts.py`) is an allowlist — unknown codes are
  dropped, never echoed into the system prompt.
- **Status questions** ("which lights are on?") get a code-computed answer set on
  the cloud path: `state_filter.ground_truth_block()` detects interrogative +
  category + state word and injects a GROUND TRUTH block (`_build_chat_messages`);
  the LLM only phrases it. It needs an interrogative so it never hijacks a
  command. The local provider has an English-only equivalent
  (`_maybe_state_filter_envelope`).
- **Runtime confirmation strings** live in per-language dicts keyed by base code,
  not in `translations/`: `_PAST_VERBS_*`, `_GENERIC_RAN_BY_LANG`,
  `_DONE_BY_LANG`, `_SENTENCE_FORMAT_BY_LANG` (`llm_client/command_policy.py`),
  `_CANNED_*` (`llm_client/client.py`), `_APPROVAL_*_BY_LANG` (`__init__.py`).
  English fallback; `_normalize_lang()` strips the region, so `zh-Hant` shares
  the Simplified entry.
- **Entity filtering tokenizes with `lexical.normalize()`** (NFKC + casefold) so
  "lumières" survives; category words map to domains in
  `_CATEGORY_KEYWORD_TO_DOMAIN` (English + fr/de/es/it, with accent-free
  variants). The need-pinning tokens (`_DOMAIN_NEED_TOKENS`,
  `_DEVICE_CLASS_NEED_TOKENS`, used via `_pin_needs_into_cap`) are still
  English-only, so a French automation's trigger sensor can be truncated away.
- **Streaming tool rounds carry the model's own prose forward.**
  `stream_with_tools` records text deltas into `content_blocks`;
  `append_streaming_tool_results` attaches them as `content` on the FIRST
  synthesized assistant message only. Without it the model re-narrates every round.

**Panel i18n** does not use `hass.localize()`. `_t(key, fallback)` delegates to
`localize()` in `shared/i18n.js`, which imports all 13 `translations/*.json` into
the bundle; resolution is exact locale → `en` → inline fallback, and `pickLocale()`
lowercases and strips the region. The panel reads `translations/en.json`, not
`strings.json`.

- **A `translations/*.json` edit changes the bundle** — rebuild and commit
  `panel.js` + `panel.build.json` (CI's `frontend` job fails on a stale bundle).
- `_t()` never warns: a missing key silently renders the fallback.
- New UI text uses `_t('key', 'English default')`, with the key added to `common`
  in `strings.json` and every locale in the same commit (appended in feature
  groups, ICU placeholders). Don't add hardcoded literals where a key fits.

### Frontend
- `panel.js` is the host only — properties, lifecycle, render dispatch. Features
  get their own `render-*.js`; websocket calls and mutations go in
  `*-actions.js` / `*-crud.js`; keep `panel/` files under ~400 lines.
- Configurable values come from `host._config` (via websocket), so `const.py`
  stays the single source of truth.
- Run `node build.js` in `frontend/` after any change; the bundle is committed.
- **The shell's height comes from HA's panel container, which has none.**
  `ha-panel-custom` renders us block-level with no height, so `:host { height: 100% }`
  resolves to `auto` and overlays (app menu, conversations drawer) clip at the
  bottom of the active tab's content. `sizePanelContainer()`
  (`shared/panel-container.js`, first in `connectedCallback`) gives the container
  `height: 100%`; `disconnectedCallback` releases it via `releasePanelContainer`
  on the container captured at connect, because HA reuses ONE `ha-panel-custom`
  for every custom panel.
- **The device safe areas are ours.** The registration sets `handle_safe_area`, so
  HA adds no container padding. `layout.css.js` pads `:host` left/right/bottom;
  the TOP inset belongs to `.header` (added to its height) so the header runs
  under the status bar. Horizontal uses `--safe-area-content-inset-*`; every inset
  reads HA's variable with an `env()` fallback (the companion app reports insets
  only through the variables).

## Testing

`pytest tests/` (setup in `CONTRIBUTING.md`; the PHCC pin `<0.13.368` matters — it tracks the latest stable core)
and `npm test` in `frontend/`. Tests are one file per module or feature. Every
test's hass gets a private, empty `config_dir` (`conftest._private_config_dir`):
PHCC's shared `testing_config` made tests see each other's files.

**`tests/test_option_coverage.py` fails when Home Assistant grows an option.** It
reads core's own schemas (the settings pages' registry commands, automation and
script schemas, each helper's create schema) and requires every option to have
a tool or a recorded reason. A failure after raising the PHCC pin is a decision
to make — support the option or list why not — not a regression.

**`tests/chat_harness.py`** drives `selora_ai/chat` and `selora_ai/chat_stream`
end to end, stubbing only the provider round trip (`architect_chat` /
`architect_chat_stream`) and keeping a real `LLMClient`, `ConversationStore` and
`automations.yaml`. Use it whenever a turn's behaviour spans more than one step —
a helper returning the right value proves nothing about a handler that fails to
pass it to the LLM, persist it, or send it to the panel, and each of those has
shipped as a bug. A `ChatTurn` exposes `asked` (kwargs the LLM received), `done`
(the terminal payload) and `await harness.messages()` (what a reopened session
loads).

```python
harness = await ChatHarness.create(hass)
first = await harness.chat("turn the plug on at midnight", reply=_proposal())
await harness.save_proposal(first.done["automation_message_index"], "selora_ai_aaa")
harness.write_automations([AQUA_ENTRY])
second = await harness.chat("change the time to 7am", reply=_proposal())
assert second.done["refining_automation_id"] == "selora_ai_aaa"
```

**Deploy skew.** A deploy without a restart leaves Python modules loaded while the
panel bundle is served fresh, so the panel calls old websocket schemas and new
keys fail with `extra keys not allowed`. `code_stamp.py` hashes every `*.py`
(contents, not mtimes — `rsync -az` preserves them) and `selora_ai/version_status`
compares it, plus the bundle's build id, so the panel can show a restart banner.

## Feature design notes

Each feature's rules, and the failures behind them, are in `docs/dev/`. **Read
the matching note before changing that feature** — most of its rules exist
because the obvious implementation shipped and broke something quietly.

- `chat-tools.md` — tool lanes and schema sizing, adding a chat or delete tool,
  MCP tool derivation, leaked tool-call markup.
- `automation-proposals.md` — chat proposals, accept-time create vs update,
  follow-up edits, MCP refinement.
- `resumption.md` — continuing a turn after the user confirms a card.
- `answer-length.md` — how-to answer shape, the prose budget, cut-off replies.
- `read-tools.md` — what inventory reads expose, entity search, configuration reads.
- `dashboards.md` — Lovelace reads and edits, panel-executed create/delete.
- `groups.md` — group helpers.
- `scenes.md` — which entities a scene may hold, and saying what it left out.
- `blueprints.md` — blueprint reads and blueprint-backed automations.
- `registry-tools.md` — areas, floors, entities, scripts, labels, categories,
  diagnostics.
- `mcp-service-calls.md` — any service over MCP, gated by risk and `confirmed`.
- `config-yaml.md` — editing configuration/package/theme YAML over MCP.
- `config-files.md` — reading and writing www/, themes/, templates and dashboard files over MCP.
- `hacs.md` — searching, installing and removing HACS cards, themes and integrations over MCP.
- `telemetry.md` — anonymous telemetry, adding a counter.
- `alexa-credential.md` — the OS-delivered Alexa voice credential.
- `recipe-updates.md` — updating an installed recipe to a newer catalog version.
- `selora-local-prompts.md` — the bundled Selora AI Local prompts are pinned
  copies of a model release; never edit their wording here.

A feature with rules of its own gets its own note there, not a section here.

