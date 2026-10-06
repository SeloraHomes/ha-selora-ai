# Dashboards

`dashboard_manager.py` backs the chat tools that read and edit Lovelace content —
`list_dashboards`, `get_dashboard`, `get_dashboard_card`, `add_dashboard_view`,
`update_dashboard_view`, `remove_dashboard_view`, `insert_dashboard_card`,
`update_dashboard_card`, `remove_dashboard_card`, `move_dashboard_card`,
`group_dashboard_cards` — plus the panel-executed `create_dashboard` /
`delete_dashboard`. It is separate from `recipes/dashboard.py` (the recipe install
stage) and reuses only its `_view_card_lists`.

## Creating and deleting dashboards

`DashboardsCollection` is a local inside `lovelace.async_setup`, never put in
`hass.data`; core's only lovelace service is `reload_resources`. But the
`lovelace/dashboards/create` handler is a bound method of the
`DashboardsCollectionWebSocket` holding it, wrapped in `require_admin` /
`async_response` (both `functools.wraps`), so `_dashboards_collection` recovers it
with `inspect.unwrap(handler).__self__.storage_collection` — identical from 2025.1
to current core.

- **It is HA's layout, not an API**, so it is type-checked (`isinstance`
  `DashboardsCollection`) and a miss reports "create it in Settings > Dashboards"
  rather than raising. `tests/test_dashboard_entry.py` pins it against the
  installed core.
- **The collection id is not the url_path.** It is HA's `slugify` of it, with
  underscores (`basement-pool` → `basement_pool`); look items up by `url_path`.
- **MCP creates and deletes on the spot** (`selora_create_dashboard` /
  `selora_delete_dashboard`), admin-gated, through `async_create_dashboard` /
  `async_delete_dashboard`. Both run the proposal's validation first, so the two
  surfaces refuse the same requests in the same words. Create seeds the document
  (see below) and, when seeding fails, does not point the caller at
  `add_dashboard_view`. **A non-admin may not create an admin-only dashboard** —
  a write-scoped credential need not be an HA admin, and the dashboard would be
  hidden from it on creation, unseedable and unfillable. Delete re-compares the stored item against the proposal's
  `expected` under `DASHBOARD_LOCK`, since resolving awaits a read and a
  dashboard remade at the same path answers to the same id. Their MCP
  descriptions REPLACE the chat ones (`_MCP_DESCRIPTIONS`): the chat text
  describes a Create button and a result that arrives later.

- **Changing a dashboard's settings runs directly on BOTH surfaces**
  (`update_dashboard` → `async_update_dashboard`): title, icon,
  `show_in_sidebar`, `require_admin` — nothing is lost and each can be set back,
  so no card. The url_path is not updatable (HA's update schema lacks it). It
  needs a collection item: the default Overview has one only once HA has
  migrated it to a `lovelace` entry; an unmigrated default and YAML dashboards
  are refused with where to change them. A non-admin may not set
  `require_admin` (it would hide the dashboard from them), and an omitted
  setting is left alone — booleans stay `None` when absent.

Creating and deleting in chat still defers to the panel: the server validates
and proposes a closed intent; the panel performs it after the user taps; the
panel reports back.

- **In chat, panel sessions only, declared by the CALLER, not the model.**
  `panel_only` on the `ToolDef` withholds the schema unless
  `_get_tools_for_provider` gets `panel_available=True`, which the three panel
  entry points pass (chat handler, its correction round, streaming path). Default
  False. **`for_assist` is not the same question** — an MCP `selora_chat` turn has
  no panel either. A fact the
  model cannot observe must not be a condition it applies: told "only in a panel
  chat", it refused users sitting in the panel. The tool's PRESENCE carries it and
  the description states availability flatly.
- **The panel never runs a websocket payload the MODEL authored.** It receives a
  closed, validated intent (`{kind: "create_dashboard", title, url_path, …}`) and
  constructs the fixed `lovelace/dashboards/create` call itself. That boundary is
  the whole security model.
- **It is carded because it is a deferred client-side privileged operation**, not
  because creation is destructive (create/update elsewhere execute directly). The
  panel must report the real result, or Selora claims success for something that
  has not happened.
- **The card IS the ask, so the tool must be CALLED, not described.** The prompt's
  REVIEW rule ("The approval card IS the confirmation step") covers every
  confirmation-carded tool — dashboards and every delete — in one block, so a new
  tool does not miss it. Descriptions must not invite narrating the result.
- **Validate everything HA's create schema can reject BEFORE the card**, with HA's
  own validators (`cv.icon`), not lookalike regexes.
- **The slug comes from HA's `slugify`**, which transliterates ("Кухня" →
  `kukhnia`, "Küche Öl" → `kuche-ol`); an ASCII class drops non-Latin titles
  entirely. Its `"unknown"` fallback is caught, or `"!!!"` lands at `/unknown`.
- **One `command_approval` fits per message.** A client action proposed beside a
  service call, delete or destructive action LOSES (it can be re-requested) and
  is NAMED in the reply, folded into the "I have not touched …" notice after the
  winning card is built (both builders replace `response`). **The model's own
  payload counts as competing**, not just the tool log: an explicit
  `command_approval` with calls, or a `command`/`delayed_command` with calls,
  reaches its slot by a different route in `synthesize_approval_from_tool_log`,
  and weighing only the log discarded those calls. An empty `calls` list does not
  count.
- **The card's prose is deterministic, never the model's.**
  `_build_client_action_response` overrides `response` with
  `client_action_pending_hint` (as the delete card does), since the model has
  typically narrated the dashboard as created already. The outcome line
  (`dashboard_created_line`) is written only once the panel reports back. A safe
  write that already executed in the same round is acknowledged beside the hint,
  with its entity tiles stripped.
- **The proposal carries the RESOLVED turn language**, not `hass.language`.
  `parse_streamed_response` re-runs `resolve_reply_language` because
  `architect_chat_stream` resolves into a local the streaming caller cannot reach.
- **The result handler** resolves the proposal to its MESSAGE INDEX
  (`_find_pending_approval`) before `set_approval_status`, which addresses by
  position; `append_message` takes `role`/`content` separately. It also checks
  `approval_kind` and that the reported kinds answer the stored descriptors — the
  panel is trusted to report faithfully, not to say which approval it reports on.
- **Panel-side rules** (`client-actions.js`):
  - The re-entry guard is synchronous, before any await — a double click lands
    both handlers before Lit disables the button, and idempotence alone does not
    stop two concurrent creates.
  - The action is idempotent: create may succeed and the report fail, leaving the
    card pending after a refresh. `create_dashboard` checks the dashboard list
    first and reconciles — comparing **every field** the create would set
    (title, icon, `require_admin`, `show_in_sidebar`), not just `url_path`, since
    the path may since have been taken by an unrelated dashboard (reported as a
    collision naming what is in the way).
  - A failed REPORT must not undo a succeeded ACTION (reloading would restore the
    pending button). The session id is captured before the first await.
- **A created dashboard has no stored DOCUMENT**, and `LovelaceStorage` reports
  that as `mode: auto-gen` — every write would be refused with the Take control
  note. A successful report calls `async_initialize_created_dashboard`, which
  saves `{"views": []}`. It **never overwrites** an existing document, and a
  missing entry is a debug line, not a failed report.
- **Deleting a dashboard** follows the same shape (card says **Delete**, deny
  tone — wording follows the action). HA keys the delete by collection **id**, so
  the panel resolves it from the list; an already-deleted dashboard reports DONE.
  A YAML dashboard is refused with where to change it.
  - **The descriptor carries an `expected` block** (raw stored title, icon,
    `require_admin`, `show_in_sidebar`), compared through the same
    `matchesProposal` the create uses. A dashboard's id is derived from its
    `url_path`, so a replacement made at that path between proposal and tap
    answers to every handle. A card with no `expected` (older proposal) still deletes.
  - **The default is refused by IDENTITY, not name** — `/default` is a path a user
    can have. The target is resolved and compared against
    `_lovelace_dashboard(hass, None)`.
  - **The blast radius is counted, and UNKNOWN is not zero.** An unreadable
    document does not block the delete, but the counts are OMITTED and the card
    says contents unknown (a generated Overview is full of cards). The count
    includes cards inside containers (`_cards_in_tree`), not just addressable
    ones (`_flat_cards`).
- **The card is a VARIANT of the one confirmation card**
  (`render-approval-card.js`, `_CONFIRM_VARIANTS`: `delete`, `destructive`,
  `client_action`), carrying accent, head icon, copy, rows and — for
  `client_action` only — its button, since the others get Allow / Deny from
  `msg.quick_actions`. `renderApprovalCard` asks whether the kind HAS a variant.
  A test fails on any class no stylesheet defines, and it **globs** the
  stylesheets rather than listing them.
  - The button is `renderConfirmChip` from `quick-actions.js` (`tone: "approve"`),
    the same component as Allow / Deny — not a `.btn-primary`.
  - The head icon says what the CARD is (`mdi:gesture-tap`); the row icon says
    what the THING is, from a per-kind map.
  - The row label is composed in the frontend from the descriptor's parts, not
    its English server-built `label`.

## Resources (custom card JS)

`dashboard_resources.py` backs `selora_list/add/remove_dashboard_resource` (MCP
only). A resource is code every user's browser runs with their HA session, so
**origin decides the gate**: a same-origin path (`/hacsfiles/…`, `/local/…`) is
added directly; an external `https://` URL needs `confirmed: true` (honouring the
`approval_required` opt-out, as service calls do); `http:`, `data:`,
`javascript:` and protocol-relative `//host` are refused.

- **A local path must be one a browser will not reinterpret** (`_LOCAL_PATH_RE`):
  plain path characters only, no `.`/`..` segments, `//`, `%` or backslash. A
  browser reads `\` as `/` (`/\evil.example/x.js` loads from evil.example) and
  `%2e` as `.`, so anything looser lets an external or recipe-owned URL pass as
  an ordinary local one — the string checked must be the string loaded.
- **Duplicates are refused by bare URL** (query/fragment stripped — HACS appends
  `?hacstag=`): HA does not deduplicate, and a module loaded twice throws on its
  second `customElements.define`. Adds share `recipes/resources._INSTALL_LOCK`.
- **`/selora_ai_resources/` belongs to recipes** (downloaded, verified, pruned by
  `recipes/resources.py`): listed with `managed_by_recipe`, never added or
  removed here.
- **YAML-mode resources are read-only** (`ResourceYAMLCollection`), reported as
  `editable: false` with where to edit them.
- Read through `_registered_items`, which calls `async_get_info` first — an
  unloaded storage collection reads as empty.

## Tool descriptions

- **`add_dashboard_view` must neither claim to create a dashboard nor deny that
  anything can.** It says what it does and points at `create_dashboard`; a
  contradiction inside one schema does not resolve as the newer half winning. A
  test holds the whole family to denying nothing. Its result carries a
  percent-encoded `url` (a stored path like `kitchen#lights` would otherwise read
  as a fragment).
- **A guard about which dashboard must not be phrased around what was asked for.**
  The "not a dashboard" warning is scoped to appending to some OTHER dashboard;
  giving a just-created one its first page is the tool's job. A resumed turn
  replays "create a new X dashboard", and a guard phrased around the ask forbade
  the view. `resolve_view`'s empty-dashboard error names `add_dashboard_view`, or
  the model relays it as a manual step for the user.
- **A page and its cards are ONE write.** `add_dashboard_view` takes `cards`,
  validates every one (`_card_type_error` / `_entity_error`, as
  `insert_dashboard_card` does) and stores the view only if all pass — otherwise
  a refused card leaves an empty page the user must delete. Empty `cards` is
  absent; a single card object is accepted. Both surfaces marshal through
  `add_view_kwargs` in `tool_executor`.
- **`get_dashboard_card` is not needed to move or remove a card**, and its
  description says so.

## Card validation

Lovelace has no server-side validator — it stores anything and the frontend
renders "Unknown type encountered: fan" on the wall. Three checks stand in:

- **A domain used as a card type is refused.** The check is INVERTED: "is this a
  domain in THIS home (live state machine) with no card of its own?" An allowlist
  of HA's card catalogue goes stale every release and can never contain custom
  cards. What remains to maintain is `_DOMAIN_NAMED_CARDS` (twelve names). Newer
  card types and `custom:` cards pass; a fanless home cannot catch `fan`.
- **The check walks the whole card**, following `cards`, `sections` and the
  conditional card's singular `card` — the bad type is usually a child — but NOT
  `features` (tile features have their own vocabulary).
- **Entity ids are validated.** `_unknown_entities` walks the whole card (`entity`,
  `entities`, nested `cards`, tap actions), carrying a list's parent key so
  `entities: ["light.one"]` is checked. Strings are shape-checked against
  `_ENTITY_ID_RE` first — in `entity`/`entity_id` too — because a custom card may
  hold a TEMPLATE there (button-card `[[[ … ]]]`, Jinja), and an `entities` row
  may be a label or divider.
- **The vocabulary is handed over before composing** (`dashboard_cards.py`) on
  `get_dashboard`'s result, once per turn. Kept short (it shares the 16K budget);
  card options are omitted since the model knows Lovelace's schemas.
- **A refused write is never reported as done.** The refusal returns as an
  ordinary tool result so the model can correct itself in-turn. When ALL of a
  turn's writes were refused, `note_failed_dashboard_write` states the outcome and
  sets `validation_error` / `validation_target` for the panel. Writes only.
- **A stored card need not be a dict** — `_flat_cards` yields whatever is in the
  list, so `isinstance` before any mapping method.

## Document semantics

- **Views have no identity.** `title` and `path` are not unique, so
  `resolve_view` accepts an index, path or title and **refuses an ambiguous name**
  with candidate indices. Ambiguity is collected across both fields at once (a
  name can match one view's title and another's path); two fields on the same
  view are one target.
- **A sections view keeps cards elsewhere** (`view["sections"][n]["cards"]`; a
  top-level `cards` is ignored). Cards are addressed by a **flat index across
  every card list in the view** (`_flat_cards`).
  `add_dashboard_view(sections=True)` seeds one grid section, since a sections
  view with none drops the first card added.
- **`ConfigNotFound` from a storage dashboard means AUTO-GENERATED, not empty.**
  The frontend renders the original-states strategy meanwhile, so reading `{}`
  reported zero views and a write replaced the visible Overview. The generated
  config cannot be materialised server-side, so `_load_config` returns
  `_AUTO_GEN_NOTE` pointing at Take control. Guarded inside `_load_config` so every
  tool inherits it. Two writers bypass `_load_config` and probe separately:
  `async_insert_card` and `recipes.dashboard.async_place_card` (which seeds a
  one-view `Home` on a genuinely blank dashboard). The probe fails **closed**.
- **A stored `strategy` is not an empty dashboard.** The Map and friends store
  `{"strategy": {...}}` and `async_load` succeeds; saved views would be ignored.
  `_load_config` refuses, reads included.
- **A missing YAML file is a read error.** `LovelaceYAML.async_load` raises
  `ConfigNotFound` while its mode stays `yaml`, so the auto-gen probe says nothing;
  `async_get_info` returns an `error` key naming the path. YAML dashboards are
  otherwise readable with `editable: false` and a note, never reported missing.
- **`_load_or_reason` is the single classifier**, asked by `_load_config` and
  `list_dashboards`, so `editable` cannot advertise a dashboard every write
  refuses. `async_place_card` checks `is_strategy_document` itself.
- **`None` is not "the default dashboard" — `"lovelace"` may be.** HA is
  migrating the default Overview to a real entry keyed `"lovelace"` (YAML mode
  uses that key too), leaving `dashboards[None]` as an unregistered empty
  placeholder. `helpers.default_dashboard_key` prefers `"lovelace"`, else `None`;
  both `dashboard_manager` and `recipes/dashboard.py` use it, and
  `list_dashboards` hides the placeholder.
- **`_load_config` DEEP-copies** — `async_load` returns HA's live cached config,
  and writers mutate before validating, so a shallow copy lets a rejected edit
  stick in the cache. Readers copy too. `recipes/dashboard.py` does the same.
- **The title is metadata** (`config.config`), read via `_dashboard_title`.
- **Setting and clearing a view field are separate arguments.** `_opt_str` reads
  blank strings as absent (models pad optional params), so
  `update_dashboard_view` takes a `clear` list.
- **`add_dashboard_view` reports its index off the FILTERED (dict-only) list**,
  which is what every reader indexes.

## Access

- **A dashboard's own `require_admin` is enforced per read.** HA hides such a
  dashboard from non-admins, while the read tools are available to non-admin chat
  users and read-only MCP credentials. `_hidden_from_caller` reports it as ABSENT
  (a refusal would confirm it exists), and it is dropped from `list_dashboards`
  and from not-found "Available:" lists.
- **Identity travels in ContextVars**: `helpers.CALLER_IS_ADMIN` /
  `CALLER_CAN_WRITE`, opened by `caller_scope` at both dispatch sites
  (`ToolExecutor.execute`, MCP `call_tool`). Default False — a call that never
  opens the scope gets LESS. `requires_admin` on a `ToolDef` gates the tool; this
  gates the object.
- **A confirmation's second leg must re-open the scope.**
  `_handle_websocket_resolve_approval` runs after the building scope ended, so the
  removal would be refused for the admin who tapped. It reads
  `connection.user.is_admin` rather than passing `True`. Any new
  post-confirmation path needs the same wrapper.
- **`list_dashboards` covers every dashboard and is not admin-gated** (the reads
  aren't). `editable` reports whether THIS CALLER could write, via
  `CALLER_CAN_WRITE` — **not** `CALLER_IS_ADMIN`, since a custom MCP token or a
  write-scoped JWT can mutate without being an HA admin (`_check_tool_access`).
  MCP answers it with `_can_access_tool` across every dashboard mutation, derived from
  `_DERIVED_MCP_TOOLS`. A generated Overview is not editable regardless.
  `recipes.dashboard.list_writable_dashboards` (the recipe placement picker) is
  unchanged.

## Concurrent edits

- **`DASHBOARD_LOCK` (`helpers.py`) is shared with `recipes/dashboard.py`.** Both
  write whole documents. `async_place_card` holds it across load→mutate→save;
  `async_remove_cards` for its whole multi-dashboard sweep. One lock for all
  dashboards: writes are rare, and the default dashboard answers to `None`, `""`
  and `"lovelace"`, so a per-target key would get it wrong.
- **The Lovelace UI writes the same document**, outside our lock, so an index from
  one call means nothing by the next. Every card edit carries a content
  fingerprint (`card_fingerprint`) and every view-index mutation a
  `view_fingerprint`, re-checked against the freshly loaded document right before
  the save: removal, `update_view`, `group_cards` (all its card indices are
  relative to the view as read) and `move_card` (whose card fingerprint pins only
  the source). `get_dashboard` hands out the fingerprint per view. Content hashes,
  not counts — counts collide. Removal drops the resolved **object**, not the
  index.
- **`remove_dashboard_view` takes `expected_fingerprint` on the shared `ToolDef`.**
  Chat's card already re-checks, but MCP deletes on the spot, and MCP schemas are
  derived from the chat `ToolDef` — a guard MCP needs has to live there.
- **A recipe's tagged card can be nested, and re-install refreshes it IN PLACE.**
  `_replace_tagged` recurses (users group recipe cards into containers).
  `replace_tagged_card` substitutes the first tagged card found, drops further
  ones, and appends only a genuinely new card; `purge_tagged_cards` is the same
  walk for uninstall. A container emptied by the removal goes; one still holding a
  user's card stays. Dedup-only tests pass both ways — assert the layout.

## Moving and grouping cards

- **Reordering and grouping are separate primitives.** `insert_dashboard_card`
  only appends; a masonry view has no rows, so "side by side" is a container
  (`group_dashboard_cards`), whose config is the caller's, passed through with
  only `cards` filled. Both move card OBJECTS — a card rebuilt from a summary
  loses what the caller did not copy.
- **A card summary names the entity domains it shows** (`_card_domains`, via
  `_card_entity_ids`, capped at `_MAX_CARD_DOMAINS`, absent when none). `tile` and
  `entities` are domain-agnostic, so "move the media cards" would otherwise need a
  fetch per card and exhaust `MAX_TOOL_CALL_ROUNDS`.
- **`move_dashboard_card` takes `from_indices`.** Repeated single moves shift
  later indices under the caller. One call keeps relative order, lands cards
  contiguously, and removes **highest index first**. `expected_fingerprint`
  (one card) is refused alongside several — use `expected_view_fingerprint`. An
  empty `from_indices` is absent; both spellings disagreeing is refused.
  `from_index` is optional, which makes `_opt_index` load-bearing (`_as_index`
  would coerce absence to 0). Coercion is shared with MCP via `move_card_kwargs`.
- **A move is `pop(from); insert(to)`** with the destination re-flattened after the
  removal and NO +1 for a forward move (0 → 1 in `[A, B, C]` gives `[B, A, C]`).
  Only an index past the last card means the end.
- **A move crosses views and dashboards** (`to_dashboard` / `to_view`; omitted =
  the card's own view, or another dashboard's first page). `to_index` is optional
  for a transfer (append), required for a reorder. Get + insert + remove only
  LOOKS equivalent — the LLM re-serialises the card in between. Source and
  destination are compared by resolved config IDENTITY, not argument strings. A
  destination refusal is prefixed as the DESTINATION's.
- **A transfer has two views to pin.** `expected_to_view_fingerprint` covers the
  destination, checked before either document is mutated.
- **Destination saved first**, so a half-done transfer duplicates rather than
  loses. Both saves pass their pre-mutation snapshot to `_save` (`previous`,
  opt-in): `LovelaceStorage.async_save` updates its cache and fires the event
  *before* awaiting the store write, so a raising save has taken effect
  everywhere but the file; re-saving the original restores both.
- **Destination-first protects nothing unless the destination LANDED.**
  `Store.async_save` swallows `WriteError` / `SerializationError` and skips writes
  when read-only or stopping, and reading back hits the already-updated cache. So
  a cross-dashboard move confirms the destination against the FILE
  (`_disk_fingerprints` / `_view_fingerprints`, one read of the file, via the
  private `Store`) before removing the source, and
  rolls back if it did not land — **counted, not tested**, since an identical card
  may already be on disk. The source is then checked only to report. An
  unreadable or unexpected `Store` is treated as fine.

## Chat output

- **A dashboard turn is LINKED, not tiled.** `[[entities:…]]` tiles read as a
  preview of the saved layout, so `is_dashboard_turn` decides once and both
  `strip_entity_tiles_after_dashboard_turn` (in
  `synthesize_approval_from_tool_log`) and the chat handler's own tile appending
  honour it. Instead the reply gets `[[dashboard:<url>|<label>]]`, rendered by the
  panel as its own card, regardless of prose (models write the path as a code
  span). The pattern accepts a single-slash absolute path only.
  - **Every page the turn wrote, or none.** `_dashboard_targets_from_log` dedupes
    by url, labels per card, and suppresses **per page** (`_already_linked`,
    terminated on `|` or `]]` since `/lovelace/0` prefixes `/lovelace/01`) — a
    session replays its own earlier markers. Past `_MAX_DASHBOARD_LINKS` (5) none
    are emitted.
  - The card is **inline-flex**, so the `<br>` between markers stacks them;
    `markdown.js` keeps exactly one break between links and drops those above the
    first.
- **Reads are bounded and one view at a time.** `get_dashboard` returns cards
  only for the named view — `_find_longest_list` cannot reach cards inside view
  dicts and would pop a whole view record.
- **A card too big to return whole is refused, not truncated.**
  `get_dashboard_card` measures the assembled result against `_MAX_CARD_CHARS`.
  `_truncate_result` would trim an inner list while the fingerprint describes the
  whole card, so writing it back would silently delete the trimmed rows — and
  `expected_fingerprint` is optional on update, so omitting only the fingerprint
  is not enough.

## MCP and lanes

- **`list_dashboards` and `insert_dashboard_card` are on MCP too** — the other
  tools point clients at them. Bodies are shared (`async_insert_card`), not
  restated.
- When adding a dashboard tool, check `_ADMIN_TOOLS` / `_READ_ONLY_TOOLS` agree
  with the `ToolDef`'s `requires_admin`; `tests/test_dashboard_tools.py` asserts
  it.
- **Every dashboard tool is in BOTH tool lanes** — "add a card" classifies as
  `command`, "reorganise my dashboard" as `config`.

## View layouts

`add_dashboard_view` / `update_dashboard_view` take `layout`: `masonry` (the
default, stored as no `type`), `sections`, `panel`, `sidebar` (`VIEW_LAYOUTS`).
Without `panel` the tools could not build a full-screen page, and a model asked
to restyle one reached for another MCP server.

- **A panel page renders ONLY its first card**, full width — full-screen
  wall-panel pages put their tiles inside one `grid`/stack card. So a panel page
  with more than one card is refused (on add and on a layout change), and
  `insert_dashboard_card` refuses a second card on one (`panel_full`): stored,
  it would never show.
- **A layout change carries the cards over** (`_set_layout`): a sections page's
  cards are flattened into one list; a list becomes one grid section.
- `sections=true` still works, as `layout='sections'`.
