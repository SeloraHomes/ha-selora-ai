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
- **`AreaEntry` exposes `.id`, not `.area_id`** (`FloorEntry` has `.floor_id`,
  entity/device entries `.area_id`). Getting it wrong is an `AttributeError`
  surfacing as "Tool execution failed".
- **Renaming an entity_id rewrites nobody's references.** HA does not touch
  automations, scripts, scenes or dashboards, so `async_update_entity` refuses
  `new_entity_id` while anything references the old id and names the referrers
  (via `group_dependents`, which is entity-generic). `new_name` (the friendly
  name — what "rename this" means) is always allowed.
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
- **Storage-collection helpers are read-only from chat.** The
  `input_*`/`counter`/`timer`/`schedule` collections are locals inside each
  component's `async_setup`, never published to `hass.data`, so there is no
  in-process way to create one. They are reachable by an authenticated websocket
  client, like dashboard entries (see `dashboards.md`), but each domain would need its
  own allowlisted command, schema and validation. `list_helpers` finds existing
  helpers to wire automations to; `create_helper` is absent rather than faked.
