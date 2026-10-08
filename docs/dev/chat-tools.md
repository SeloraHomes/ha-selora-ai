# Chat tool surface

## Tool lanes

**Lanes apply to LOW-CONTEXT providers only.** A cloud turn gets the whole schema
(~17.7k tokens with 72 tools, against a fraction of that per lane). Lane regexes that guessed wrong made the model
report a capability as nonexistent ("I can't create areas directly"), and a schema
that never varies caches, which a per-turn lane prevents.

- **`_cloud_intent_hint` asks the provider's `holds_full_tool_schema`** — not
  locality and not `is_low_context` (which means ≲2K and describes Selora AI Local
  alone). OpenRouter is a cloud gateway that may front an 8K model, and Ollama
  serves whatever window the runtime was started with; handing either a ~9.7K
  schema gets the request REJECTED.
- **It defaults to False and is answered per MODEL.** Model fields are free-form;
  `gpt-4` (8K), `gpt-3.5-turbo` (16K), `gemini-1.0-pro` (32K) and
  `google/gemma-2-9b-it` are all selectable. `model_is_known_large`
  (`providers/base.py`) is one **allowlist** of families known ≥128K, matched on
  the family after stripping any `vendor/` prefix. OpenAI, Gemini and OpenRouter
  ask it; Anthropic may answer by catalogue (every Claude is ≥100K). Otherwise the
  base rule needs a REPORTED `context_window` ≥ `FULL_SCHEMA_SAFE_WINDOW`;
  `None` means UNKNOWN and keeps the conservative behaviour.
- **Prompt caching covers the system prompt only — a safety property.** Entity
  states ride in the CURRENT TURN'S USER MESSAGE (`_build_chat_messages`), never
  the system prompt or tool schemas, so a cache hit can never replay a stale
  reading of the house. Moving state into the system prompt must move the cache
  breakpoint with it. Anthropic marks the system block; OpenRouter does so for
  `anthropic/*` models only (`cache_control` is an Anthropic extension); OpenAI
  caches long prefixes automatically.

### Lanes on low-context providers

`TOOL_LANES` (`tool_registry.py`) maps an intent hint to a tool subset; an absent
or unknown hint gets the full schema. `LLMClient._cloud_intent_hint` tests
**`config` first**, then `command`, because a registry request matches no question
or automation pattern and `_classify_chat_intent` falls through to `command` — the
device-control lane, which lacks the registry tools. `config` is a separate lane
rather than more `COMMAND_TOOL_NAMES` because the sets barely overlap; only the
entity-resolution tools are in both.

- `_is_config_request` (`llm_client/intent.py`) is **separate from
  `_classify_chat_intent`**, whose four return values map to trained LoRA
  specialists. A false negative is cheap (full schema); a false positive strips
  `execute_command` from a device command, so every pattern requires vocabulary a
  command has no reason to use. The live-area-name fallback closes the gap for
  "move the lamp to the Study" — but only when the area is the DESTINATION of a
  movement (`_MOVE_DESTINATION`), and a power-command syntax vetoes it ("put the
  Study lamp on").
- Script *creation* is not claimed by the config lane — "create a script that
  turns the lights off at 11pm" is automation-shaped. Only management verbs are.
- `delete_area` / `delete_script` / `delete_label` are in **both** lanes, like
  `delete_automation`, since "get rid of the Movie Night script" classifies as
  `command`.
- **Every registry tool is `large_context_only=True`.** The low-context path sets
  `tool_executor = None`, but `_get_tools_for_provider` is also reachable from
  Assist, and a 1.7B model handed a registry-editing schema will call it.

## Adding a delete tool

`_DELETE_TOOLS` and `_DELETE_KINDS` (`llm_client/command_policy.py`) are both
allowlists: add a new delete tool to **both** plus a branch in `_resolve_approval`
(`__init__.py`). Missing either fails silently — the tool returns
`requires_approval`, the loop short-circuits and discards the prose, the
synthesizer drops the descriptor, and the user gets an empty reply and no card.

**A replace or delete hands back what it overwrote.** The result is the only
undo a caller has, so it carries `previous`, through `helpers.attach_previous`:
withheld as `previous_omitted` when the whole result would not fit, never cut by
`_truncate_result` into something that only looks restorable.

- **In the terms the tool that undoes it takes**, not as stored: a counter's
  `min`/`max`, a schedule's `schedule`, a group update's `new_name`, a page's
  `layout` and `options`, plus `clear` for settings the edit added — passing
  the old values back leaves an added one in place.
- **Tools go in `_RETURNS_PREVIOUS`** (`mcp_server/definitions.py`), which tells
  MCP callers to keep it and drops "cannot be undone" from the delete note. A
  removed page or deleted dashboard goes in `_RETURNS_STORED`: its copy is the
  stored Lovelace configuration, which no tool writes back whole, and the
  description says so. So does a result whose copy no tool can replay (sections
  a layout change dropped, pages a strategy replaced).
- **Content, not identity**: something deleted and made again gets a new id. A
  scene's editor metadata and a person's login link and picture are left out,
  since no tool sets them.
- `remove_dashboard_card` returns its card as `card` (`card_omitted`), as its
  description says.

**MCP definitions are derived** from the chat `ToolDef`s (`_DERIVED_MCP_TOOLS` /
`_mcp_tool_from_chat_tool` in `mcp_server/definitions.py`), never restated — a second copy
drifts quietly, with the MCP client rejecting an argument chat accepts. The
deriver:
- drops `_PANEL_ONLY_PARAMS` from `properties` AND `required` (a schema requiring
  an undefined property is invalid and strict clients reject the whole tool);
- appends a correction to any tool in `_DELETE_TOOLS` / `_DESTRUCTIVE_TOOLS`: the
  chat description promises a confirmation card, but MCP has none and executes on
  the spot.

**The MCP `serverInfo.version` is `<manifest version>+<tool-set hash>`**
(`_async_server_version`). A client may keep the tool list it already has while
the version is unchanged, and the manifest moves only on release. The hash covers every definition's
name, description and schema, so touching a tool moves it with no manual bump.

## Leaked tool markup

`tool_markup_leak` fires when a model writes its tool-call syntax as plain text
(`<invoke …>`, `<｜DSML｜…`) instead of a real tool_use block. It is stripped
non-streaming by `strip_leaked_tool_markup` and mid-stream by `MarkupLeakGuard`
(`llm_client/parsers.py`), both called from the tool loop in `llm_client/client.py`.

- **The delimiter is not always ASCII.** DeepSeek fences special tokens with
  U+FF5C FULLWIDTH VERTICAL LINE (`<｜tool▁calls▁begin｜>`) and uses U+2581 for
  underscores; both look ASCII in a bug report. `_LEAK_PIPE` / `_LEAK_SEP` hold
  the character classes; `_leak_marker_prefix_could_match` folds U+2581 to `_` so
  a half-arrived `tool▁ca` is held back mid-stream. Fixtures in
  `tests/test_tool_markup_leak.py` must be built from codepoints — an editor that
  normalizes the glyph turns the test back into the ASCII case.
- **A leak is a failed tool call, not a final answer.** No provider parses a
  text-form call, so the loop would read the round as committed and end the turn
  mid-investigation. Both loops detect the shape (`leak_guard.suppressed`, or
  `strip_leaked_tool_markup` changing the text), append the stripped prose plus
  `_LEAK_RETRY_DIRECTIVE`, and `continue` — bounded by `_MAX_LEAK_RETRIES` (2)
  independently of the round budget.

## Deterministic shortcuts are for the low-context model

`_pre_provider_short_circuit` answers some turns before the provider is
called. The safety refusal (prompt injection) runs for every provider. The
command and clarification helpers — "turn off all the lights" built into a
command, "turn it off" answered with a "Which light?" picker — run only when
the provider `is_low_context`: they exist because the 1.7B local model invents
service calls for those phrasings. A cloud model resolves them itself, with
tools, history and the home in context, in any language; the helpers are
patterns for the phrasings someone coded, so on that path they could only
pre-empt an answer the model would have got right.

That only holds because a cloud model can do a multi-device request in ONE
response. A response whose tool calls are all `execute_command` ends the turn
(the loop writes the confirmation from the results instead of paying for
another round), so "turn off all the lights" must arrive as every call at once:
`execute_command` takes `entity_ids` (folded into `entity_id` by
`execute_command_arguments`), and its description states the end-of-turn rule —
the description travels with every prompt variant, slim command prompt included.
With one entity per call, a cloud model sent one call, the turn ended, and only
one light went off.

- **A safe call covers a whole category** (`_MAX_TARGET_ENTITIES`, 50). A call
  that needs approval stays at `_MAX_APPROVAL_TARGET_ENTITIES` (3), so the card
  names a handful of devices. A per-call cap the model can split around only
  costs time: at 3, sixteen lights took seven calls.
- **A round of commands that commute runs at once**
  (`commands_run_together`): one idempotent verb (no `toggle`), the same
  data, distinct targets, no scene/script — a group can share members with
  another target, so only calls that agree on the end state may race.
  Each call waits up to `_STATE_SETTLE_TIMEOUT` for its devices to report, so
  run in turn the waits add up. Anything else keeps the model's order.
- **The confirmation has one sentence per action**, not per call
  (`build_executed_confirmation`).

## Home health in chat: one tool, not one per page

Every cloud turn carries every chat tool's schema, so a capability the MCP side
splits across several tools comes to chat folded where the questions are the
same. `check_system` (`system_check.py`) answers "is anything wrong / what
needs updating?" in one call — integrations not working, open repairs, pending
updates, backup health — from the managers the per-topic MCP tools use, each
section capped. With `reload_integration` it costs ~240 tokens per turn;
the four MCP tools it stands for would cost several times that.

- **Removals are one tool too**: `remove_from_home` (`removal_cards.py`) takes a
  `kind` — device, entity, integration, blueprint — and returns a delete card
  (all four kinds in `_DELETE_KINDS`). The preview is each kind's own removal
  check asked without confirmation, so chat refuses what MCP refuses; the card
  carries the target's identity (device id, entity registry id, entry_id,
  blueprint file hash), which the confirmed removal re-checks. MCP keeps its
  per-kind tools, whose `confirmed` contract differs from a card.
- **Repairs**: `fix_repair` (destructive card) and `ignore_repair` — see
  `registry-tools.md`.
- **Adding a chat tool moves its MCP definition to `_DERIVED_MCP_TOOLS`** —
  `reload_integration` was hand-written for MCP and is now derived, so the two
  cannot drift (see "Adding a delete tool").
