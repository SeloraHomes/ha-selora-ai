# Groups

`group_manager.py` backs `list_groups`, `create_group`, `update_group`,
`delete_group`, so the LLM can offer a group when an automation would repeat a
long entity list — the automation then targets one stable entity_id and the user
edits membership in Settings → Helpers.

- **Helper config entries, not YAML.** We drive HA's own `group` config flow
  (`flow.async_init("group")` → **menu** step `{"next_step_id": <group_type>}` →
  form). The first step is a `SchemaFlowMenuStep`, so the form-only loop in
  `recipes/ws.py` does not work. State lands in `entry.options` (`group_type` /
  `name` / `entities` + per-type extras); `entry.data` stays empty. This is
  deliberately not the legacy `group:` YAML route recipes use
  (`recipes/renderer.py`, scoped to a package file).
- **Per-domain by construction.** A helper group holds one domain, so
  `infer_group_type()` refuses mixed light+switch membership with guidance.
  `sensor`/`number`/`input_number` combine into a numeric `sensor` group, which
  *requires* a `type` statistic (default `mean`).
- **Inapplicable per-type options are policed in code** — `_build_create_payload`
  can forward an option only to schemas that accept it (others are
  `vol.PREVENT_EXTRA`). Ask whether a user could have meant it for a type that
  cannot store it:
  - `requires_all_members: true` is **rejected** off
    `binary_sensor`/`light`/`switch`: on other types it is meaningful ("closed only
    when every cover is"), so reporting success would claim an ignored setting.
    **`false` is dropped**: it is already the behaviour, and the schema's
    `default: false` would otherwise dead-end every cover/lock/fan/valve group for
    clients that materialize defaults. The drop happens *before* the update path's
    no-op guard, or a `false`-only update reloads, writes nothing and reports
    `updated`.
  - `statistic` is **dropped** off `sensor` (debug log): "mean of two lights" is
    not a request anyone makes, and refusing dead-ended "group my two lights". Its
    value is validated *after* the drop.
  - **A new per-type option needs one of these two treatments.**
- **`entities` and `add_entities`/`remove_entities` are mutually exclusive** —
  replacement and delta are different intents; applying one and dropping the
  other would report success having ignored part of the request.
- **An empty optional argument is absent** (`_is_empty_delta`, `new_name`
  normalization). Models emit `[]` / `""` for unused params; otherwise
  `add_entities: []` beside a rename refuses the rename, and beside `entities`
  trips the exclusivity check. `entities: []` keeps its refusal — emptying a group
  is a real request, and the error points at delete.
- **A stored member may be a registry id, not an entity_id.** HA's entity selector
  (`cv.entity_id_or_uuid`) keeps whichever form it was given, so a UI-created
  group can hold uuids. `_resolve_members()` serves three rules:
  - **Compare resolved.** Domain inference, the self-reference guard, removal
    matching, addition dedup and the added/removed diff all run on entity_ids —
    raw comparison breaks renames, misses removals, duplicates additions, and
    reads a same-list replacement as all-removed/all-added (unhiding members).
  - **Store the form already on record.** A retained member keeps its stored
    representation (`stored_by_entity_id`) — a uuid survives an entity_id rename;
    only new members are stored as given.
  - **Report resolved.** `describe_group` / `list_groups` and update results
    return entity_ids.

  A stored id whose entity was **deleted** stays unresolved, and
  `async_update_group` **refuses any update while one is present**: the group
  platform re-runs the list through `er.async_validate_entity_ids` on setup, which
  raises, leaving the entity `unavailable` while the entry still reports `LOADED`
  — so a plain rename would brick the group and report `updated`. The error names
  the exact stale string (the caller's only handle); `remove_entities` with it, or
  a replacement list omitting it, clears the block. Reads still show the raw id.
- **Unhiding a member is conditional on the other groups.** `hide_members` hides
  the *entity*, and an entity can sit in several hidden groups, so both unhide
  paths check `_members_free_to_unhide()`:
  - *Update* — a removed member is released only if no other `hide_members` group
    lists it.
  - *Delete* — `group.async_remove_entry` unhides unconditionally, so
    `_hides_to_restore_after_delete()` captures still-claimed members **before**
    removal and re-applies the hide, restricted to `hidden_by == INTEGRATION`.
- **A `hidden_by == USER` entity is never touched, in either direction.**
  `_apply_member_visibility` skips it: unhiding undoes the user's choice, and
  re-hiding transfers ownership to the integration so a later removal releases it.
  Creation runs HA's real flow, whose `_async_hide_members` writes `INTEGRATION`
  unconditionally, so `async_create_group` captures `_user_hidden_members()` first
  and `_restore_user_hides()` after.
- **`describe_group` caps `members` at `_MAX_LISTED_MEMBERS`** (`member_count`
  exact, `members_omitted` the difference). `ToolExecutor._find_longest_list` only
  looks at top-level lists and lists inside top-level dicts — never inside a list
  *of* dicts — so one oversized group made it pop the whole `groups[0]` record.
  Any tool returning records with inner lists has the same exposure.
- **A numeric group refuses a member that reports text** (`_non_numeric_members`,
  on create and update). With `ignore_non_numeric` False, `SensorGroup` silently
  drops the member from the calculation. `unknown`/`unavailable` are allowed — a
  sensor reads both while offline.
- **A group may not contain its own entity.** HA's options flow prevents it
  (`entity_selector_without_own_entities`); we bypass that flow, so
  `async_update_group` checks `own_entity_ids()`. Nesting a *different* group is
  legal.
- **No store.** Groups are read live from config entries. `unmanaged_yaml_groups()`
  surfaces legacy `group.*` entities read-only. `resolve_group()` checks the YAML
  case **before** the "no group helpers yet" shortcut, and on the by-name path too
  — otherwise a YAML-only home is told its visible group is missing and offered a
  duplicate.
- **Updates must reload.** `group` registers no update listener and we bypass the
  options flow, so `async_update_group` calls `async_reload` itself.
- **Create/update execute directly; delete goes through the confirmation card**
  (`kind: "group"`, `target_id` = the immutable `entry_id`, so no fingerprint).
  The card label carries the blast radius from `group_dependents()`, because the
  tool-loop short-circuit discards the model's prose. Four referrers: automations
  and scripts (`automations_with_entity` / `scripts_with_entity`), **scenes**
  (the scene state's `entity_id` attribute — there is no `scenes_with_entity`),
  and **parent groups**, which do not break when a child is deleted but silently
  shrink. Parents need two disjoint lookups, unioned: `parent_groups()` walks
  helper entries, `_yaml_parent_groups()` (HA's `groups_with_entity`) walks legacy
  YAML groups — and recipes write YAML groups, so a helper nested in one is
  ordinary.
- Adding a tool here touches `group_manager.py`, `mcp_server.py` (`_tool_*` +
  `MCPTool` schema + name constant + handler map + `_ADMIN_TOOLS` /
  `_READ_ONLY_TOOLS`), `tool_registry.py` (`ToolDef` + `CHAT_TOOLS` +
  `COMMAND_TOOL_NAMES`), and `tool_executor.py`. `COMMAND_TOOL_NAMES` matters:
  group phrasings classify as `"command"`, which trims the low-context schema to
  that set.
