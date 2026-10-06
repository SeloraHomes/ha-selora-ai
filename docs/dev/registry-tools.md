# Registry, script, label, category and diagnostic tools

`registry_manager.py`, `script_manager.py`, `label_manager.py`,
`category_manager.py` and `diagnostics_tools.py` back the config-management half
of the chat tool surface — tools that reshape the home rather than operate it, so
the model stops reciting Settings click-paths.

## Areas, floors, entities

- **Floors are full CRUD; deleting one is a confirmation card.** (`_ensure_floor`
  also creates one when an area names a storey that does not exist.) HA's area
  registry clears each area's `floor_id` on the floor-removed event, silently, so
  the card NAMES the affected areas rather than counting them. `floor_id` is
  derived from the name like `area_id`, so it is reusable once the floor is gone;
  the descriptor carries `created_at` for the confirm handler to re-check.
- **`list_floors` orders by `level`, unset LAST.** Level is the only field
  carrying the storeys' real relationship, and unset is not the ground floor.
  `_opt_level` exists because the blank-is-absent rule would read `level: 0` as
  unset.
- **An entity's area is an override of its device's area.** When the entity's
  device is *already* in the target area, `async_assign_area` **clears** the
  entity's `area_id` so it inherits; only otherwise does it write the override.
  Pinning would strand the entity in the old room next time the device moves. The
  result reports `entities_assigned` and `entities_now_inheriting` separately so a
  blank `area_id` read back is not taken as failure.
- **From HA 2026.10 an entity with no name of its own cannot have an area of its
  own** — the device's main entity takes the device's area, and the registry
  raises `ValueError` on any user edit of `area_id` or `name` that would leave
  one. `async_assign_area` reports it per entity in `failed` (naming the device
  to move) so the rest of the call still lands; `async_update_entity` returns it
  as an error. Uncaught, it was "Tool execution failed" with devices already
  moved.
- **`AreaEntry` exposes `.id`, not `.area_id`** (`FloorEntry` has `.floor_id`,
  entity/device entries `.area_id`). Getting it wrong is an `AttributeError`
  surfacing as "Tool execution failed".
- **Renaming an entity_id rewrites nobody's references.** HA does not touch
  automations, scripts, scenes or dashboards, so `async_update_entity` refuses
  `new_entity_id` while anything references the old id and names the referrers
  (via `group_dependents`, which is entity-generic). `new_name` (the friendly
  name — what "rename this" means) is always allowed.
- **Exposure is changed only for an assistant the hub uses** (`entity_exposure.py`):
  Assist always, Alexa when Selora's or Home Assistant Cloud's skill is linked
  (both read `cloud.alexa`), Google only with Home Assistant Cloud. HA shows the
  cloud columns only to a Cloud account, so a change elsewhere is a setting
  nobody can see or undo. A refusal writes nothing else in the call either, and
  exposure is written only after the registry update succeeds.
- **Reading exposure must not write it.** Core's `async_should_expose` records the
  default it computes into the entity's options, pinning it for good;
  `async_get_exposure` reads the recorded setting or works the default out from
  core's rule without recording it.
- **Resetting is `clear`, on every update tool** (entity: name, icon, area;
  device: name, area; area: icon, floor; floor: icon, level). A blank value is
  "not set" (`_opt_str`), so without it a renamed device never got its vendor
  name back and an entity moved to a room never followed its device again.
  `_clear_error` refuses an unknown name and a field set and cleared in one
  call. The argument readers (`mcp_server/registry.py` `_update_*_kwargs`) are
  shared by chat, MCP, the preview and the confirmed card.
- **Deleting an area unassigns, silently** — automations targeting
  `area_id: living_room` keep loading and match nothing. Hence the card, with
  counts in its label.
- **Uniqueness checks ask the registry's own name lookup, never a forgiving
  resolver.** `create_floor`'s resolver matches aliases, so a floor named after
  another's alias was once refused though HA enforces uniqueness on names only.

## Scripts

- **`scripts.yaml` is a mapping keyed by object_id**, not a list like
  `automations.yaml`, so `script_manager` does not reuse `automation_utils`'
  readers (it does reuse `_quote_yaml_booleans` and `_to_plain_types`).
- **`set_script` replaces wholesale** — call `get_script` first when editing.
  HA's `async_validate_config_item` runs **before** the write. A *reload* failure
  after a successful write is reported as `reload_error` beside the write, not
  raised, since the change did land.
- **Settings not passed are kept; `clear` removes them** (`CLEARABLE`). `fields`
  and `variables` replace the script's own when given. Rebuilding the config
  from the parameters alone stripped a parameterised script's inputs and broke
  every caller.
- **Changing fields names the callers** (`check_callers`), and on chat the
  replacement card says the inputs change or go. Fields are what callers pass;
  a required one added, or one removed, fails those calls at run time.
- **`script_dependents` also finds `action: script.<name>` calls.** HA's
  reference tracking follows `entity_id` targets only, so the usual way of
  calling a script was invisible — to this and to the delete card's warning.
  It walks the loaded automations' and scripts' `raw_config`.

## Labels and categories

- **Label assignment is deltas, never replacement.** Several unrelated concerns
  write labels; a replacement from a model that only knows `holiday` would drop
  `battery-powered`. `assign_labels` **creates** an unknown label (it has no
  contents, so refusing protects nothing) — the opposite of areas, where a typo'd
  auto-create would split a home in two.
- **Categories are labels with a scope.** HA keeps a list per page scope, and an
  entity holds **at most one per scope** (`RegistryEntry.categories` is
  `{scope: category_id}`). Consequences:
  - Names are unique only within a scope, on `name.casefold()` alone, so
    `"Outdoor Lights"` and `"Outdoor  Lights"` can coexist. `resolve_category`
    matches HA's comparison first, then falls back to collapsed-whitespace
    matching only when unambiguous. `create_category` compares casefolded names
    directly — the loose match would call an allowed name a duplicate.
  - The scope is required, not searched; `assign_category` writes one scope's key
    and leaves the rest. The delete card's `target_id` is
    `"<scope>#<category_id>"`. A `category_id` is a ULID, so no timestamp
    fingerprint is needed.
  - **An entity the scope's page never lists cannot be filed there**
    (`_SCOPE_DOMAINS`) — it would be counted and seen nowhere. Only known scopes
    are policed. **`helper` cannot be answered from the domain**: template,
    utility-meter, derivative and threshold helpers are plain `sensor.*` /
    `binary_sensor.*`, so membership comes from the config entry's integration
    declaring `integration_type: helper` (the question `helper_overview` asks).
  - **The scope check applies only when ASSIGNING.** Clearing removes an existing
    mapping, often a stale one; refusing would make the bad state unfixable.
  - Scope strings are frontend-owned and free-form server-side; the Helpers page
    uses **`helper`, singular**. `UI_SCOPES` is a schema enum, not a server-side
    refusal, so a scope a future HA adds still works.
  - No rename, mirroring `label_manager`.

## Diagnostics and helpers

- **`get_logs` reads `hass.data[DATA_SYSTEM_LOG].records`** — the value is the
  logging handler; the deduplicated ring is `.records`.
- **Traces are keyed `automation.<config id>`**, not by entity_id;
  `_resolve_trace_key` translates via the state's `id` attribute. A YAML
  automation with no `id` is never traced and gets that explanation.
- **Storage-collection helpers are created by the PANEL in chat** (`create_helper`,
  `helper_manager.py`), as dashboards are. The `input_*`/`counter`/`timer`
  collections are locals inside each component's `async_setup`, published only
  through its admin-only `<domain>/create` websocket command, so the chat tool is
  `panel_only` and proposes a `client_action` (`kind: "create_helper"`).
  - **Validated with the component's OWN create schema** (its collection's
    `CREATE_UPDATE_SCHEMA`; `SCHEMA` on input_number), so the Create button
    never fails on something HA rejects, and the fields sent are what HA stores —
    which is how the panel recognises its own retry. A timer duration is sent as
    `H:MM:SS` for that reason.
  - **Two allowlists**: the backend keeps only the schema's keys (inapplicable
    ones are dropped), `HELPER_FIELDS` in `client-actions.js` picks the command
    and copies only its keys. `min`/`max` are `minimum`/`maximum` to a counter.
  - **A name in use is refused at proposal time** — HA would suffix the id and
    the user would get two helpers of one name.
  - **The outcome names the entity_id only if it checks out**
    (`_created_helper_entity_id`: proposed domain, live state, proposed name);
    the panel is not trusted to say what its work was done to.
- **Changing and deleting a storage helper** (`update_helper` / `delete_helper`,
  `helper_manager.py`) goes through the same collection on both surfaces.
  - **HA's helper update REPLACES the stored item** (`_update_data` returns the
    id plus the validated update, nothing else). The stored item is merged with
    the change and the whole validated with the component's schema first;
    passing only the changed fields wipes the rest. `clear` removes an optional
    setting, since a blank value reads as "not set".
  - **The item is found through the entity registry** (platform = domain,
    unique_id = item id). No item means a YAML helper; a config-entry helper is
    not in a collection at all — each is refused with where to change it.
    Changing or removing a config-entry helper is removing an integration.
  - **Update runs directly, delete is carded in chat** (kind `helper`, in both
    `_DELETE_TOOLS` and `_DELETE_KINDS`). The entity_id is derived from the name,
    so the card carries a content fingerprint (`helper_fingerprint`) that the
    delete re-checks with no await in between. The card and the MCP result name
    what used it (`async_helper_dependents`: automations, scripts, scenes,
    groups, dashboards) — HA rewrites no references.
  - A rename onto another helper's name is refused, as at creation.
- **Zones are storage helpers here** (`zone` in `_COLLECTIONS`, schema
  `CREATE_SCHEMA`): the same collection shape, so the helper tools create,
  move and delete them rather than a zone toolset of their own. `zone.home` is
  not stored — HA draws it from the home's location — and is refused with
  where to change it (Settings → System → General).
- **Schedules are storage helpers too** (`schedule` in `_COLLECTIONS`, schema
  `SCHEMA`). The tool takes one `schedule` object (`{day: [{from, to}]}`)
  rather than seven parameters; `_expand_schedule` spreads it into the per-day
  keys, and HA's schema checks the blocks (order, overlap). A day named wrongly,
  or a `schedule` that is not an object, is refused, not dropped: dropped, the
  schedule is created and never comes on. An update keeps the days not named.
- **A creatable domain needs the panel's allowlist too** (`HELPER_FIELDS` in
  `frontend/src/panel/client-actions.js`), or chat's Create button always fails.
  `test_the_panel_creates_every_helper_the_backend_proposes` compares the two.
- **Every other helper runs its own config flow, through the same tool**
  (`helper_flow.py`), the way `group_manager` drives `group`'s — config-entry
  helpers need no panel. One tool, not one per helper: template entities of
  every type, utility meters, thresholds, derivatives, min/max all go through
  `create_helper` with `domain` = the integration. **The flow is the schema**:
  a call without `fields` returns the menu choices (`type`) or the form's
  fields, serialized with HA's own `cv.custom_serializer`; a call with them
  submits the form, so HA's validation decides. Only integrations declaring
  `integration_type: helper` are driven — a device or cloud-account flow is not
  one a chat should complete — and `group` is sent to `create_group`, which
  polices what a generic flow cannot. **A setup of several forms or menus is
  walked one step per call** (`flow_sessions.async_drive`): every answer that
  leads to another step returns it with a `flow_id`, and the next call passes
  it back; a rejected answer keeps the same form open for a corrected one. A template alarm panel with no state
  template is optimistic — it holds and restores its own state — and offers a
  mode only when it has an action for it, which the tool description says.
  - **The form is serialized with the library `cv` itself uses**
    (`_to_field_list`): `cv.to_field_list` (probatio) from HA 2026.9, else
    `voluptuous_serialize.convert`, imported lazily. 2026.9 dropped
    `voluptuous-serialize` from its requirements, and `cv.custom_serializer`
    answers "unsupported" with its own library's sentinel, which the other
    library returns in place of the field list.
- **Flows held open between calls** (`flow_sessions.py`, shared by helper
  setups, options and repair fixes):
  - **Only flows started here, for what they were started for**, can be
    continued — an owner tuple per flow. Any other flow_id (the user's own flow
    in the UI, a discovery, another entry's options) is refused.
  - **Nothing lingers.** Each step re-arms a `FLOW_TTL` timer that aborts the
    flow; unload aborts them all. The timer's job is `cancel_on_shutdown` — a
    stopping Home Assistant drops every flow anyway, and the test harness counts
    any other timer still armed at teardown as lingering.
- **MCP creates both kinds on the spot** (`selora_create_helper`). A storage
  helper goes through `async_create_helper`, which runs the proposal's
  validation (a name in use included) and then the collection recovered from
  `<domain>/create` by `helpers.registered_storage_collection` — the dashboards'
  mechanism (`dashboards.md`), identical for all eight domains from 2025.1 on and
  pinned per domain by `tests/test_mcp_storage_helpers.py`. The entity_id comes
  from the entity registry by item id, since the collection suffixes on a clash.
  Its definition (`TOOL_CREATE_HELPER_DIRECT`) takes the chat tool's params minus
  `remaining_intent`, so the two cannot drift, and says nothing of a Create button.
- **An unknown entity that is not a device gets no device list.**
  `_humanise_unknown_entity_error` answers a mistyped light by listing the home's
  lights and locks; a missing alarm panel or helper is named plainly instead.

## Integrations (MCP)

`integration_manager.py` lists config entries and reloads, enables/disables,
removes or reconfigures one, through `hass.config_entries` as Settings →
Devices & services does.

- **Selora AI's own entry is refused for every change.** Disabling, removing or
  reloading it cuts the connection the request came in on, and its options hold
  the AI provider credentials, which are never configured automatically.
- **Options go through the entry's own options flow**, driven like
  `helper_flow` drives a config flow: no `options` describes the form (with each
  field's `current` value, from its `suggested_value`), `options` submits it.
  Multi-step options flows are walked by `flow_id`, as a helper's setup is.
  - **A credential's value is never described** — options forms pre-fill
    stored passwords (HEOS does). A password selector or a credential-like
    name reports only `is_set`, its default dropped too.
  - **Sections are described recursively** (`fields` under an `expandable`),
    or a form like `scrape`'s cannot be filled in.
  - **`{}` is a submission**, not "describe": some forms take it on purpose.
- **A disable or enable whose unload/load failed says `require_restart`**, as
  Home Assistant's own `config_entries/disable` does.
- **Removing always asks first** (`requires_confirmation` with the device and
  entity counts) — Home Assistant rewrites no automation that used them.
- Listing is read-only access, as `config_entries/get` is in Home Assistant;
  every change needs admin.

## Removing devices and entities (MCP)

`registry_removal.py` puts a confirmation in front of removal and adds the
entity counterpart.

- **A device goes through `device_removal`**, the Health card's path: released
  from its ONE owning integration (whose hook may refuse) or nothing changes. A
  device several integrations share, or one whose integration has no removal
  hook, is refused up front — Settings is where the user picks an owner.
- **An entity is removed only once its integration no longer provides it**
  (no state or a `restored: true` one, while its integration is LOADED and the
  entity is not disabled), which is when HA's UI offers "Remove". Neither signal
  holds alone: a disabled entity has no state, and HA gives every entity of a
  failed, retrying or starting integration a restored one — each comes back.
  Disabling hides one still provided.
- **Child devices (2026.9+)**: a parent's preview names its parts and their
  entities, since they go with it; a part's own id is refused (it has its own
  removal API, and the release-and-detach path fails halfway on it).
- **A confirmed entity removal carries the preview's `registry_id`** — an
  entity_id is a name a rename frees for another entity.
- Both answer `requires_confirmation` first with what uses them — HA rewrites no
  automation that referred to them. Selora AI's own device and entities are
  refused.

## Updates (MCP)

`update_manager.py` lists `update.*` entities waiting to be installed and reads
one's release notes. Over MCP, installing, skipping and un-skipping are services
(`update.install`, `update.skip`, `update.clear_skipped`), gated by risk there.

- **Chat installs through a destructive card** (`install_update`, kind
  `update`, verb `install`). The card names both versions, the backup and the
  restart (core/OS/supervisor). Its fingerprint is what the user approves —
  the update's registry entry id (an entity_id can be taken by another), the
  version named, and the backup promised — and each is re-checked on confirm: a
  newer version, a replaced entity, or a backup that can no longer be made
  refuses rather than installing something else. The install pins `version`
  where the entity supports that, and is not awaited — it can take minutes, and a core update
  restarts HA under the request; progress shows on the entity.

- **Grouped as HA's "Update all" groups them**: `home_assistant` (the hassio
  core/os/supervisor entities, by unique_id prefix), `app` (other hassio),
  `hacs`, `device`, `other`.
- **Release notes keep their lines** — they are markdown — so they get their own
  cleaner rather than `sanitize_untrusted_text`, which collapses whitespace:
  control characters stripped, blank runs shrunk, length bounded. Admin-gated,
  as HA's `update/release_notes` is.

## Backups (MCP)

`backup_status.py` reads the backup manager as `backup/info` does: backups
newest first, the last completed / attempted automatic backup, the next run,
and a `warning` when the last attempt postdates the last success or none has
completed for a week.

- **Only the part common to every supported core is required.** 2025.1
  introduced the manager; later cores added its `state` and the schedule's next
  run, read when present. Sizes come from the per-location status (newer) or
  the backup itself (older).
- **No failure warning while `state` is `create_backup`**: HA records the
  attempt when a backup starts, so a running one looks like a failed one.
- `automatic` stays `null` when HA cannot tell (imported or older backups);
  dates are compared as instants, not strings. Admin-gated, as `backup/info` is.
- Creating is a service (`backup.create_automatic`, or `hassio.backup_full` on
  a supervised install), already callable over MCP. Restoring is not offered.

## Repairs (MCP)

`repairs_manager.py` lists the issue registry's open repairs, ignores one, or
runs its fix flow.

- **Titles and descriptions are rendered** from the CREATOR's (`issue.domain`)
  `issues` translations in the home's language, placeholders filled, as the
  Repairs page shows them — `issue_domain` only names who the issue is about.
- **A fix is walked step by step across calls**: the first call starts it and
  returns its first step — the step's own `fix_flow.step.<id>` title and
  description, fields or menu choices — with a `flow_id`; each later call
  answers that step (`fields`, or `choice` for a menu). Fixes start with menus
  and run several forms, so a single submission cannot cover them.
  - Held open by `flow_sessions`, like a helper's setup. A browser (external) step returns
    its URL to finish in Settings → Repairs.
- Listing is read-only access, as `repairs/list_issues` is in Home Assistant;
  ignoring and fixing need admin. `repairs` is an `after_dependency` for
  hassfest, since the fix flow manager comes from it.
- **In chat, a fix is a destructive card** (`fix_repair` in `_DESTRUCTIVE_TOOLS`,
  verb `fix`, kind `repair` in `_apply_destructive_actions`), and only for HA's
  STOCK `ConfirmRepairFlow` — the one fix known to finish on its single
  confirmation. An empty first form proves nothing (HA's legacy subscription
  fix confirms, then goes to an external step), so the flow's class is read
  off `manager.async_create_flow`, which builds it WITHOUT running a step —
  starting it (`async_init`) runs the first step, which can already do the fix's
  work. Checked at preview and again on confirm, and the fingerprint is
  re-checked after the flow starts (that awaits). Anything else is sent to
  Settings → Repairs. The card's fingerprint hashes the issue's
  creation time, data, placeholders and severity — an integration can update an
  issue in place, keeping its id and `created`. `ignore_repair` runs
  directly (it is undone the same way), and its MCP definition is derived.

## Apps (MCP)

`apps_manager.py` lists installed apps (formerly add-ons) and reads an app's
logs. Start/stop/restart are services (`hassio.app_*` with `{app: slug}`;
`hassio.addon_*` with `{addon: slug}` on older cores), already callable.

- **Logs go through HA's `hassio` client** (`send_command("/addons/<slug>/logs")`),
  whose path check refuses anything that normalizes differently; the slug is
  also matched against `^[a-z0-9_]+$` and the installed list first.
- **Last N lines only** (100, max 500), ANSI and control characters stripped,
  each line bounded. `HassioAPIError` is a `RuntimeError`, not a
  `HomeAssistantError`.
- Both admin-gated, as HA's app pages are — app logs print credentials.
  `get_apps_list` is the newer alias of `get_addons_list` (2025.1).
