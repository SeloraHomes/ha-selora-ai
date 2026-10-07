# Read tools

## What the read tools can see

**`COLLECTOR_DOMAINS` is not the home.** It is the set worth snapshotting and
pattern-analysing, and doubles as half of the safe-command allowlist
(`__init__.py`), so a domain with no service-table entry — `camera` — is absent by
design. Gating the read tools on it once made a home's cameras invisible to every
inventory tool, and the model reported "no camera entities" as a fact about the
house.

- **Discovery is not permission, and not analysis.** `is_inspectable_entity`
  (`entity_capabilities.py`) answers the read tools; `COLLECTOR_DOMAINS` still
  answers the collector, pattern engine, `health_monitor` and the in-prompt entity
  list. `execute_command` polices commands separately.
- **It is a DENY-list of four plumbing domains** (`conversation`, `stt`, `tts`,
  `wake_word`). A wrongly hidden entity is reported as absent; a wrongly shown one
  is a row the caller ignores — and an allow-list defaults unknown domains to
  hidden. `is_actionable_entity`'s per-domain exclusions still apply.
- **The in-prompt entity list stays allowlist-coupled, and the prompt says so**:
  the list carries controllable domains only, a tool result is equally valid
  ground truth, and an absence is reported only when a search confirms it.
- **A widened read must not bulk-export secrets.** `input_text` / `text` hold
  their value AS state. `_display_state` withholds the value of any entity HA
  marks `mode: password` — and only that; the entity stays listed.
  `execute_command`'s post-state is exempt (it reports a value the caller set).
- **`get_entity_state` returns every attribute**, not a per-domain pick — a
  vacuum's battery or an integration's extra readings were invisible. Bounded
  instead: text capped, lists and mappings cut at 50 before converting, nesting
  past 3 levels replaced (never stringified — a stringified mapping carries its
  credential keys), and a total budget, charged while converting, past which
  attributes are named in `attributes_omitted`. Attributes named like a
  credential are left out and URL `token`/`authSig` parameters stripped: a
  camera's `entity_picture` carries the token that opens its stream, and this
  read is open to read-only credentials.
- **An entity's area is its device's unless overridden** — `get_home_snapshot`
  resolves through the device registry, as `search_entities` does.

## Entity search

`_tool_search_entities` (`mcp_server/entities.py`) is the resolution tool for chat and MCP
and the only route to entities the snapshot leaves out, so an empty result is
load-bearing.

- **The haystack indexes the entity AND its device.** Entity names carry the
  product, never the brand ("IKEA" is the device's `manufacturer`).
  `_device_search_index` walks the device registry once per call and contributes
  name, `name_by_user`, manufacturer and model, plus the area fallback.
- **Device text joins term coverage, not the fuzzy component.** Fuzzy is the typo
  rescue; a token-set ratio against a longer haystack scores the same typo lower,
  so folding device text in would push matches under `SEARCH_FUZZY_FLOOR`.
- **Any filter stands alone; only a call with none of the three is refused.**
  "Every battery entity" is a real request with no name (batteries are diagnostic,
  so `EntityFilter.is_active` keeps them out of the snapshot). The listing ceiling
  is what makes a bare filter safe. Device class is read from the live attribute,
  then the registry, so an offline flat battery still matches.
- **An empty result says so.** `searched` names the indexed fields and `hint`
  states that a miss is a failed lookup, not an absent device, with what to try
  next. `_tool_strategy_recipe` carries the same two facts (the snapshot omits
  diagnostic entities; entity names carry no brand).
- **Matches echo `manufacturer` / `model` / `device_class` when set**, so fuzzy
  near-misses can be told apart; omitted when empty.
- **Filters: domain, device_class, area, state, label**, each usable alone.
  `area` takes an area or a floor (every area on it), matched against the
  entity's area or its device's; `label` reaches what a `label_id` target does:
  the entity's labels, its device's, and its area's. An unknown
  area or label is refused with what exists.
- **A password-mode entity never matches `state`.** Its state is the secret, and
  a match would confirm a guess, though the value itself is never returned.
- **MCP's `selora_search_entities` is derived** from the chat ToolDef, with its
  own description (`_MCP_DESCRIPTIONS`: the chat text assumes the in-prompt
  entity list). A hand-written copy is how a filter reaches one surface only.
- **A ranked search and a filter-only listing have different ceilings.** A query
  is a resolution — default 10, max 25. A `device_class`-only call is a listing —
  returns all, up to 50. Past the bound, `omitted` + `omitted_note` say the list
  is partial.

## Configuration reads

`get_automation`, `get_scene`, `get_automation_traces` and `find_references`
(`config_inspect.py`, `diagnostics_tools.py`) answer "what does X do / why
didn't it". Without them the model answered from names and descriptions, which
drift from behaviour, and defended the answer.

- **Resolve within the kind the user named.** `resolve_domain_ref` (`helpers.py`)
  searches one domain only: a scene, a script and an automation share names
  freely ("Goodnight" scene, "Goodnight Scene" automation). A name matching
  several entities is an error that lists them, never the first hit.
- **Traces are read for scripts too** (`get_automation_traces`). An entity_id
  or object id of either kind is taken as written (checked with
  `valid_entity_id` first: the state machine lowercases, so a NAME like
  "Bedtime" would match `script.bedtime`); otherwise every exact name across
  both kinds is collected at once, and more than one is refused — an ambiguity
  in either kind must not fall through to the other's runs. A script's trace key is `script.<unique_id>`
  from the registry, which survives an entity_id rename.
- **A run stopped in a condition ends on a leaf** (`…/entity_id/0`), a string
  inside the step. `stopped_at.config` is the nearest enclosing mapping — the
  step as written — while `result` stays the leaf's (state vs wanted state).
- **A trace step carries its config and result.** `last_step` alone
  (`condition/0`) is an index into a config the model never saw. `stopped_at`
  reads the extended trace: the config the run used at that path and the step's
  result. `_step_config` maps trace paths' singular keys onto either config form
  (`condition` / `conditions`, a mapping or a list).
- **automations.yaml is not every automation.** Packages and includes load too;
  `_loaded_automation_yaml` reads the entity's `raw_config`, JSON round-tripped
  so the loader's line-number subclasses don't dump as `!!python` tags.
- **The loaded config has `!secret` values resolved**, so it is read only behind
  the chat `get_automation`, which is admin-only like core's `automation/config`.
  MCP `selora_get_automation` is open to read-only tokens and does not fall back.
- **A scene changes only what it lists**, and the result says so — that is the
  answer to "why didn't my scene turn X off". Scenes outside `scenes.yaml` report
  their target states from the loaded entity (`scene_config.states`).
- **`find_references` folds in the entity's device.** Device triggers and device
  targets name the device, not the entity the user sees.

## History and statistics

`get_entity_history` / `selora_get_entity_history` (`history_reader.py`; the MCP
schema is derived from the chat one) read the recorder for up to 10 entities.

- **Every bound is reachable past.** Ranges go to 31 days of states or 400 of
  statistics; a page is the newest `limit` rows and `older` / `next_offset` lead
  to the rest. A fixed window with a silent cut answered "when did it last…"
  wrongly whenever the answer was just outside it.
- **A history read holds at most `_ROW_CAP` rows per entity, off the event
  loop** (`_fetch_changes`, in the recorder's executor). The recorder's `limit`
  keeps the OLDEST rows, so a range holding more is narrowed toward its end
  only while the newer half alone still overflows, then walked forward in capped
  chunks, keeping the newest; the answer carries
  `range_start` / `range_note`. Fetching every row of a month before paging put
  hundreds of thousands of `State` objects in memory on a busy sensor.
- **Statistics are the long answer.** States are purged after `purge_keep_days`;
  statistics are not. Defaults follow the dashboards: `change` for a meter
  (`has_sum`), mean/min/max for a measurement. An entity without a
  `state_class` has none and is pointed at `source='history'`, not handed `[]`.
- **A removed entity is read, not refused** — its past is often the question.
  An id with no state, no registry entry and no rows gets a note pointing at
  `search_entities`.
- **Password-mode text entities read as `***`**: their history is the secret's
  past values. Secrecy is read from the recorded rows too, not only the live
  state — an entity removed or taken out of password mode still recorded its
  secret while it was one.
- **Bucket count is capped before the query** (`_MAX_BUCKETS`): the cost is the
  query, not the page, so a year of 5-minute buckets is refused with a coarser
  period suggested.

## Camera snapshots (MCP)

`selora_get_camera_image` answers with an MCP **image** block beside a small
JSON text block — a handler returns `ToolImage` and `_dispatch` turns it into
both. Everything else stays text.

- **Admin-gated though read-only**, like logs and traces: a camera shows the
  inside of the home, and a read-only credential is the one most often handed to
  an outside assistant.
- **Scaled to 1280 × 720 by default.** Home Assistant scales only a JPEG, and
  only when given BOTH sides, so both are always passed; over 4 MB is refused
  with a request for a smaller size.
- **The camera component is imported at call time** and only once
  `camera` is loaded: it pulls in image libraries an install without cameras
  may not have. `image/jpg`, which many cameras report, is sent as `image/jpeg`.
