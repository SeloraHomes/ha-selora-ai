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
- **A ranked search and a filter-only listing have different ceilings.** A query
  is a resolution — default 10, max 25. A `device_class`-only call is a listing —
  returns all, up to 50. Past the bound, `omitted` + `omitted_note` say the list
  is partial.
